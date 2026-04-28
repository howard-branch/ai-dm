"""SRD-style trap catalog + resolution.

Templates from ``assets/srd5_2/core/traps.json``. Per-scene placed
instances live on :class:`ai_dm.game.scene_state.ArmedTrap`, which
records detection / disarm / expended state.

Resolution mirrors the SRD pattern:

* If a trap has an ``attack`` block: roll ``1d20 + bonus`` vs the
  victim's AC; on hit, deal the listed damage.
* If a trap has a ``save`` block: the victim rolls; on a failure,
  takes full damage (or applies the named condition); on a success,
  takes half (when ``on_success_half=True``) or nothing.
* Optional ``secondary`` block applies additionally (e.g. dart trap
  poison) using the same save-or-attack semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ai_dm.rules.dice import DiceRoller
from ai_dm.rules.srd_core import load

TrapTrigger = Literal["pressure_plate", "tripwire", "proximity", "opened"]
TrapReset = Literal["manual", "auto", "never"]
TrapSeverity = Literal["setback", "dangerous", "deadly"]


@dataclass(frozen=True)
class TrapAttack:
    bonus: int
    damage: str
    damage_type: str


@dataclass(frozen=True)
class TrapSave:
    ability: str
    dc: int
    on_success_half: bool = False


@dataclass(frozen=True)
class TrapEffect:
    damage: str | None = None
    damage_per_10ft: str | None = None
    extra_damage: str | None = None
    damage_type: str | None = None
    aoe_radius_ft: int | None = None
    depth_ft: int | None = None


@dataclass(frozen=True)
class Trap:
    key: str
    name: str
    detect_dc: int
    disarm_dc: int
    trigger: TrapTrigger
    attack: TrapAttack | None
    save: TrapSave | None
    primary: TrapEffect | None
    secondary_save: TrapSave | None
    secondary_damage: str | None
    secondary_damage_type: str | None
    secondary_attack: TrapAttack | None
    condition_on_fail: str | None
    single_use: bool
    reset: TrapReset
    severity: TrapSeverity
    description: str


def _trap_attack(rec: dict[str, Any] | None) -> TrapAttack | None:
    if not rec:
        return None
    return TrapAttack(
        bonus=int(rec["bonus"]),
        damage=str(rec["damage"]),
        damage_type=str(rec["damage_type"]),
    )


def _trap_save(rec: dict[str, Any] | None) -> TrapSave | None:
    if not rec:
        return None
    return TrapSave(
        ability=str(rec["ability"]),
        dc=int(rec["dc"]),
        on_success_half=bool(rec.get("on_success_half", False)),
    )


def _trap_effect(rec: dict[str, Any] | None) -> TrapEffect | None:
    if not rec:
        return None
    return TrapEffect(
        damage=rec.get("damage"),
        damage_per_10ft=rec.get("damage_per_10ft"),
        extra_damage=rec.get("extra_damage"),
        damage_type=rec.get("damage_type"),
        aoe_radius_ft=rec.get("aoe_radius_ft"),
        depth_ft=rec.get("depth_ft"),
    )


def _from_record(rec: dict[str, Any]) -> Trap:
    sec = rec.get("secondary") or {}
    return Trap(
        key=str(rec["key"]),
        name=str(rec["name"]),
        detect_dc=int(rec["detect_dc"]),
        disarm_dc=int(rec["disarm_dc"]),
        trigger=rec["trigger"],
        attack=_trap_attack(rec.get("attack")),
        save=_trap_save(rec.get("save")),
        primary=_trap_effect(rec.get("primary")),
        secondary_save=_trap_save(sec.get("save")),
        secondary_damage=sec.get("damage"),
        secondary_damage_type=sec.get("damage_type"),
        secondary_attack=_trap_attack(sec.get("attack")),
        condition_on_fail=rec.get("condition_on_fail"),
        single_use=bool(rec.get("single_use", False)),
        reset=rec.get("reset", "manual"),
        severity=rec.get("severity", "setback"),
        description=str(rec.get("description") or ""),
    )


_DATA = load("traps")
_BY_KEY: dict[str, Trap] = {r["key"]: _from_record(r) for r in _DATA["traps"]}

SEVERITIES: tuple[str, ...] = tuple(_DATA.get("severities") or ())
TRIGGERS: tuple[str, ...] = tuple(_DATA.get("triggers") or ())
RESETS: tuple[str, ...] = tuple(_DATA.get("resets") or ())


def get_trap(key: str) -> Trap | None:
    return _BY_KEY.get(key)


def is_trap(key: str) -> bool:
    return key in _BY_KEY


def all_traps() -> list[Trap]:
    return list(_BY_KEY.values())


# --------------------------------------------------------------------- #
# Detect / disarm / resolve
# --------------------------------------------------------------------- #


@dataclass
class TrapResolution:
    triggered_by: str
    trap_key: str
    attack_total: int | None = None
    hit: bool | None = None
    save_total: int | None = None
    save_dc: int | None = None
    succeeded: bool | None = None
    damage_dealt: int = 0
    damage_type: str | None = None
    secondary_damage_dealt: int = 0
    conditions_applied: list[str] = None  # type: ignore[assignment]
    expended: bool = False

    def __post_init__(self) -> None:
        if self.conditions_applied is None:
            self.conditions_applied = []


def attempt_detect(trap: Trap, perception_total: int) -> bool:
    return int(perception_total) >= int(trap.detect_dc)


def attempt_disarm(trap: Trap, thieves_tools_total: int) -> bool:
    return int(thieves_tools_total) >= int(trap.disarm_dc)


def _roll_save(
    trap_save: TrapSave, actor: Any, roller: DiceRoller
) -> tuple[int, bool]:
    saves = getattr(actor, "saving_throws", {}) or {}
    mod = int(saves.get(trap_save.ability, 0))
    rr = roller.roll("1d20")
    total = rr.total + mod
    return total, total >= trap_save.dc


def _resolve_attack(
    trap_attack: TrapAttack, actor: Any, roller: DiceRoller
) -> tuple[int, bool, int]:
    rr = roller.roll("1d20")
    total = rr.total + int(trap_attack.bonus)
    ac = int(getattr(actor, "ac", 10))
    hit = rr.crit or (not rr.fumble and total >= ac)
    dmg = 0
    if hit:
        dmg = roller.roll(trap_attack.damage).total
    return total, hit, dmg


def resolve_trigger(
    trap: Trap,
    actor: Any,
    *,
    roller: DiceRoller | None = None,
) -> TrapResolution:
    """Resolve a single victim stepping into a trap."""
    r = roller or DiceRoller(seed=0)
    res = TrapResolution(
        triggered_by=str(getattr(actor, "actor_id", "")),
        trap_key=trap.key,
    )

    succeeded: bool | None = None
    dmg = 0

    if trap.attack is not None:
        total, hit, dmg = _resolve_attack(trap.attack, actor, r)
        res.attack_total = total
        res.hit = hit
        res.damage_dealt = dmg
        res.damage_type = trap.attack.damage_type
        if hit and dmg and hasattr(actor, "take_damage"):
            actor.take_damage(dmg)
        succeeded = not hit  # for secondary gating
    elif trap.save is not None:
        total, succeeded = _roll_save(trap.save, actor, r)
        res.save_total = total
        res.save_dc = trap.save.dc
        res.succeeded = succeeded
        if trap.primary and trap.primary.damage:
            dmg = r.roll(trap.primary.damage).total
            if succeeded and trap.save.on_success_half:
                dmg //= 2
            elif succeeded:
                dmg = 0
            res.damage_dealt = dmg
            res.damage_type = trap.primary.damage_type
            if dmg and hasattr(actor, "take_damage"):
                actor.take_damage(dmg)
        if not succeeded and trap.condition_on_fail:
            if hasattr(actor, "add_condition"):
                actor.add_condition(trap.condition_on_fail, source=f"trap:{trap.key}")
            res.conditions_applied.append(trap.condition_on_fail)

    # Secondary effect (poison on a dart trap, etc.)
    if trap.secondary_save is not None and trap.secondary_damage is not None:
        s_total, s_ok = _roll_save(trap.secondary_save, actor, r)
        s_dmg = r.roll(trap.secondary_damage).total
        if s_ok and trap.secondary_save.on_success_half:
            s_dmg //= 2
        elif s_ok:
            s_dmg = 0
        res.secondary_damage_dealt = s_dmg
        if s_dmg and hasattr(actor, "take_damage"):
            actor.take_damage(s_dmg)

    if trap.single_use:
        res.expended = True
    return res


__all__ = [
    "RESETS",
    "SEVERITIES",
    "TRIGGERS",
    "Trap",
    "TrapAttack",
    "TrapEffect",
    "TrapReset",
    "TrapResolution",
    "TrapSave",
    "TrapSeverity",
    "TrapTrigger",
    "all_traps",
    "attempt_detect",
    "attempt_disarm",
    "get_trap",
    "is_trap",
    "resolve_trigger",
]

