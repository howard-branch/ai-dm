"""SRD environmental hazards.

Templates loaded from ``assets/srd5_2/core/hazards.json``; per-scene
runtime instances live on :class:`ai_dm.game.scene_state.ActiveHazard`.

A hazard *resolves a tick* against a single actor: optionally rolling a
save, optionally rolling damage, and translating the catalog's
``on_fail`` token into mutations on
:class:`ai_dm.game.combatant_state.CombatantState`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal

from ai_dm.rules.dice import DiceRoller
from ai_dm.rules.srd_core import load

HazardTrigger = Literal["interval", "on_enter", "while_in", "on_event"]
IntervalUnit = Literal["round", "minute", "hour", "day"]


@dataclass(frozen=True)
class HazardSave:
    ability: str
    dc: int
    on_success: str = "no_effect"
    on_fail: str = "no_effect"


@dataclass(frozen=True)
class HazardDamage:
    dice: str | None = None
    dice_per_10ft: str | None = None
    max_dice: int | None = None
    type: str = "bludgeoning"


@dataclass(frozen=True)
class Hazard:
    key: str
    name: str
    trigger: HazardTrigger
    interval_unit: IntervalUnit
    interval_n: int
    save: HazardSave | None
    damage: HazardDamage | None
    condition_on_fail: str | None
    on_tick: str | None
    exempt_traits: tuple[str, ...]
    description: str


# --------------------------------------------------------------------- #
# Loader
# --------------------------------------------------------------------- #

def _save_from(rec: dict[str, Any] | None) -> HazardSave | None:
    if not rec:
        return None
    return HazardSave(
        ability=str(rec["ability"]),
        dc=int(rec["dc"]),
        on_success=str(rec.get("on_success", "no_effect")),
        on_fail=str(rec.get("on_fail", "no_effect")),
    )


def _damage_from(rec: dict[str, Any] | None) -> HazardDamage | None:
    if not rec:
        return None
    return HazardDamage(
        dice=rec.get("dice"),
        dice_per_10ft=rec.get("dice_per_10ft"),
        max_dice=rec.get("max_dice"),
        type=str(rec.get("type", "bludgeoning")),
    )


def _from_record(rec: dict[str, Any]) -> Hazard:
    return Hazard(
        key=str(rec["key"]),
        name=str(rec["name"]),
        trigger=rec["trigger"],
        interval_unit=rec.get("interval_unit", "round"),
        interval_n=int(rec.get("interval_n", 1)),
        save=_save_from(rec.get("save")),
        damage=_damage_from(rec.get("damage")),
        condition_on_fail=rec.get("condition_on_fail"),
        on_tick=rec.get("on_tick"),
        exempt_traits=tuple(rec.get("exempt_traits") or ()),
        description=str(rec.get("description") or ""),
    )


_DATA = load("hazards")
_BY_KEY: dict[str, Hazard] = {r["key"]: _from_record(r) for r in _DATA["hazards"]}


def get_hazard(key: str) -> Hazard | None:
    return _BY_KEY.get(key)


def is_hazard(key: str) -> bool:
    return key in _BY_KEY


def all_hazards() -> list[Hazard]:
    return list(_BY_KEY.values())


# --------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------- #


@dataclass
class HazardOutcome:
    actor_id: str
    hazard_key: str
    save_total: int | None = None
    save_dc: int | None = None
    succeeded: bool | None = None
    damage_dealt: int = 0
    damage_type: str | None = None
    conditions_applied: list[str] = None  # type: ignore[assignment]
    exhaustion_delta: int = 0
    skipped_reason: str | None = None

    def __post_init__(self) -> None:
        if self.conditions_applied is None:
            self.conditions_applied = []


def _is_exempt(actor: Any, traits: Iterable[str]) -> bool:
    have: set[str] = set()
    for src in (
        getattr(actor, "resistances", None) or [],
        getattr(actor, "immunities", None) or [],
    ):
        for t in src:
            have.add(f"resistance_{t}".lower())
            have.add(f"immunity_{t}".lower())
            have.add(str(t).lower())
    for t in traits:
        if str(t).lower() in have:
            return True
    return False


def _apply_on_fail(token: str, actor: Any, outcome: HazardOutcome) -> None:
    """Translate a catalog token like ``"exhaustion+1"`` into actor mutations."""
    tok = token.strip().lower()
    if tok in ("", "no_effect", "none"):
        return
    if tok.startswith("exhaustion+"):
        try:
            n = int(tok.split("+", 1)[1])
        except ValueError:
            n = 1
        cur = int(getattr(actor, "exhaustion", 0) or 0)
        new = min(6, cur + n)
        if hasattr(actor, "exhaustion"):
            actor.exhaustion = new
        outcome.exhaustion_delta = new - cur


def resolve_tick(
    hazard: Hazard,
    actor: Any,
    *,
    roller: DiceRoller | None = None,
    save_modifier: int | None = None,
) -> HazardOutcome:
    """Resolve one hazard tick against ``actor``.

    Rolls the save (when defined), applies damage / on_fail / conditions
    and returns a :class:`HazardOutcome` describing what happened.
    """
    out = HazardOutcome(
        actor_id=str(getattr(actor, "actor_id", "")),
        hazard_key=hazard.key,
    )
    if _is_exempt(actor, hazard.exempt_traits):
        out.skipped_reason = "exempt"
        return out

    # Save (optional)
    succeeded: bool | None = None
    if hazard.save is not None:
        r = roller or DiceRoller(seed=0)
        mod = save_modifier
        if mod is None:
            saves = getattr(actor, "saving_throws", {}) or {}
            mod = int(saves.get(hazard.save.ability, 0))
        rr = r.roll("1d20")
        total = rr.total + int(mod)
        succeeded = total >= hazard.save.dc
        out.save_total = total
        out.save_dc = hazard.save.dc
        out.succeeded = succeeded
        token = hazard.save.on_success if succeeded else hazard.save.on_fail
        _apply_on_fail(token, actor, out)

    # Damage
    if hazard.damage and hazard.damage.dice:
        r = roller or DiceRoller(seed=0)
        dmg = r.roll(hazard.damage.dice).total
        if succeeded is True and hazard.save and "half" in (hazard.save.on_success or ""):
            dmg //= 2
        if hasattr(actor, "take_damage"):
            actor.take_damage(int(dmg))
        out.damage_dealt = int(dmg)
        out.damage_type = hazard.damage.type

    # Condition on fail
    if hazard.condition_on_fail and succeeded is False:
        if hasattr(actor, "add_condition"):
            actor.add_condition(hazard.condition_on_fail, source=f"hazard:{hazard.key}")
        out.conditions_applied.append(hazard.condition_on_fail)

    return out


def apply_falling_damage(
    distance_ft: int,
    *,
    roller: DiceRoller | None = None,
    max_dice: int = 20,
) -> int:
    """Roll fall damage (1d6 per 10 ft, capped at ``max_dice``)."""
    if distance_ft < 10:
        return 0
    dice = min(int(max_dice), distance_ft // 10)
    if dice <= 0:
        return 0
    r = roller or DiceRoller(seed=0)
    return r.roll(f"{dice}d6").total


__all__ = [
    "Hazard",
    "HazardDamage",
    "HazardOutcome",
    "HazardSave",
    "HazardTrigger",
    "IntervalUnit",
    "all_hazards",
    "apply_falling_damage",
    "get_hazard",
    "is_hazard",
    "resolve_tick",
]

