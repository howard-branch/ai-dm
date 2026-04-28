"""Rests & recovery — SRD 5.2.

Single source of truth: ``assets/srd5_2/core/rests.json``.

Short rest: 1h, spend hit dice to heal, recover ``recharge="short"``
resources. Long rest: 8h once per 24h, full HP, recover hit dice
(half max, min 1), all spell slots, all short+long resources, and one
level of exhaustion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ai_dm.rules.dice import DiceRoller
from ai_dm.rules.srd_core import load

_DATA = load("rests")
SHORT = _DATA["short_rest"]
LONG = _DATA["long_rest"]
SHORT_DURATION_MIN: int = int(SHORT["duration_min"])
LONG_DURATION_HR: int = int(LONG["duration_hr"])
LONG_MAX_PER_DAY: int = int(LONG["max_per_day"])


@dataclass
class RestResult:
    kind: str  # "short" | "long"
    hp_restored: int = 0
    slots_restored: dict[int, int] = field(default_factory=dict)
    resources_restored: list[str] = field(default_factory=list)
    hit_dice_spent: dict[str, int] = field(default_factory=dict)
    hit_dice_recovered: dict[str, int] = field(default_factory=dict)
    exhaustion_after: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "hp_restored": self.hp_restored,
            "slots_restored": dict(self.slots_restored),
            "resources_restored": list(self.resources_restored),
            "hit_dice_spent": dict(self.hit_dice_spent),
            "hit_dice_recovered": dict(self.hit_dice_recovered),
            "exhaustion_after": self.exhaustion_after,
        }


def _restore_resources(actor: Any, kinds: tuple[str, ...]) -> list[str]:
    restored: list[str] = []
    res = getattr(actor, "resources", None)
    if not isinstance(res, dict):
        return restored
    for key, r in res.items():
        if getattr(r, "recharge", None) in kinds and r.current < r.max:
            r.current = r.max
            restored.append(key)
    return restored


def _restore_slots(actor: Any) -> dict[int, int]:
    out: dict[int, int] = {}
    slots = getattr(actor, "spell_slots", None)
    if not isinstance(slots, dict):
        return out
    for lvl, s in slots.items():
        before = s.current
        s.current = s.max
        if s.current != before:
            out[int(lvl)] = s.current - before
    return out


def apply_short_rest(
    actor: Any,
    *,
    hit_dice_spent: dict[str, int] | None = None,
    roller: Any | None = None,
    con_modifier: int = 0,
) -> RestResult:
    """Spend ``hit_dice_spent`` ({"d8": 2, ...}) to heal, then refresh short resources."""
    spent: dict[str, int] = {}
    healed = 0
    pool = getattr(actor, "hit_dice", None)
    if hit_dice_spent and isinstance(pool, dict) and roller is not None:
        for die, n in hit_dice_spent.items():
            entry = pool.get(die)
            if not isinstance(entry, dict):
                continue
            available = int(entry.get("current", 0))
            use = max(0, min(int(n), available))
            for _ in range(use):
                rr = roller.roll(f"1{die}")
                healed += max(1, int(rr.total) + int(con_modifier))
            entry["current"] = available - use
            if use:
                spent[die] = use
    if healed:
        if hasattr(actor, "heal"):
            actor.heal(healed)
        elif hasattr(actor, "hp"):
            actor.hp = min(int(getattr(actor, "max_hp", actor.hp + healed)), actor.hp + healed)
    restored = _restore_resources(actor, ("short",))
    return RestResult(
        kind="short",
        hp_restored=healed,
        resources_restored=restored,
        hit_dice_spent=spent,
        exhaustion_after=int(getattr(actor, "exhaustion", 0) or 0),
    )


def apply_long_rest(actor: Any) -> RestResult:
    """Full HP, recover spell slots, refresh short+long resources, -1 exhaustion."""
    max_hp = int(getattr(actor, "max_hp", 0) or 0)
    healed = 0
    if hasattr(actor, "hp") and max_hp:
        healed = max(0, max_hp - int(actor.hp))
        actor.hp = max_hp
        actor.temp_hp = 0
    if hasattr(actor, "death_saves"):
        actor.death_saves = {
            "successes": 0, "failures": 0, "stable": False, "dead": False,
        }
    slots = _restore_slots(actor)
    restored = _restore_resources(actor, ("short", "long"))
    # Hit dice: recover up to half max (min 1) per die size.
    recovered: dict[str, int] = {}
    pool = getattr(actor, "hit_dice", None)
    if isinstance(pool, dict):
        for die, entry in pool.items():
            if not isinstance(entry, dict):
                continue
            mx = int(entry.get("max", 0))
            cur = int(entry.get("current", 0))
            gain = min(max(1, mx // 2), max(0, mx - cur))
            entry["current"] = cur + gain
            if gain:
                recovered[die] = gain
    # One level of exhaustion off.
    exh = int(getattr(actor, "exhaustion", 0) or 0)
    new_exh = max(0, exh - 1)
    if hasattr(actor, "exhaustion"):
        actor.exhaustion = new_exh
    return RestResult(
        kind="long",
        hp_restored=healed,
        slots_restored=slots,
        resources_restored=restored,
        hit_dice_recovered=recovered,
        exhaustion_after=new_exh,
    )


# --------------------------------------------------------------------- #
# Rest-progress tracking (interruptible rests)
# --------------------------------------------------------------------- #


RestKind = Literal["short", "long"]


class RestProgress(BaseModel):
    """Tracks an in-progress rest until it completes or is interrupted.

    Round-trippable Pydantic model so it can live on
    :class:`ai_dm.game.combatant_state.CombatantState`. The actual
    rest *effects* (HP, slot recovery, exhaustion -1) are applied by
    :func:`complete_rest`.
    """

    model_config = ConfigDict(extra="forbid")

    kind: RestKind
    started_round: int | None = None
    started_minute: int | None = None
    elapsed_minutes: int = 0
    interrupted: bool = False
    completed: bool = False
    strenuous_minutes: int = 0
    combat_rounds: int = 0

    def required_minutes(self) -> int:
        return SHORT_DURATION_MIN if self.kind == "short" else LONG_DURATION_HR * 60

    def advance(self, minutes: int) -> bool:
        """Add ``minutes`` of resting; return ``True`` once complete."""
        if self.completed or self.interrupted:
            return self.completed
        self.elapsed_minutes += int(minutes)
        if self.elapsed_minutes >= self.required_minutes():
            self.completed = True
        return self.completed

    def interrupt(
        self,
        kind: Literal["combat", "strenuous", "damage"],
        *,
        minutes: int = 0,
        rounds: int = 0,
    ) -> bool:
        """Record an interruption; return ``True`` if the rest is now
        spoiled per SRD thresholds."""
        if kind == "combat":
            self.combat_rounds += int(rounds) or 1
            # Any combat ruins a short rest; a long rest can absorb a
            # brief skirmish unless it lasts ≥ ~1h, but for simplicity
            # mirror the SRD "any strenuous activity" rule: ≥1 combat
            # round during short rest = interrupt; ≥1 round during a
            # long rest counts toward strenuous tally.
            if self.kind == "short":
                self.interrupted = True
            else:
                # 1 round ≈ 6s; 60 minutes = 600 rounds.
                self.strenuous_minutes += max(1, int(rounds) * 6 // 60)
        elif kind == "strenuous":
            self.strenuous_minutes += int(minutes)
            if (
                self.kind == "long"
                and self.strenuous_minutes >= 60
            ):
                self.interrupted = True
            if self.kind == "short" and minutes > 0:
                self.interrupted = True
        elif kind == "damage":
            self.interrupted = True
        return self.interrupted


def begin_short_rest(
    actor: Any,
    *,
    started_round: int | None = None,
    started_minute: int | None = None,
) -> RestProgress:
    progress = RestProgress(
        kind="short",
        started_round=started_round,
        started_minute=started_minute,
    )
    if hasattr(actor, "rest_progress"):
        actor.rest_progress = progress
    return progress


def begin_long_rest(
    actor: Any,
    *,
    started_minute: int | None = None,
) -> RestProgress:
    progress = RestProgress(kind="long", started_minute=started_minute)
    if hasattr(actor, "rest_progress"):
        actor.rest_progress = progress
    return progress


def complete_rest(
    actor: Any,
    progress: RestProgress | None = None,
    *,
    hit_dice_spent: dict[str, int] | None = None,
    roller: DiceRoller | None = None,
    con_modifier: int = 0,
) -> RestResult | None:
    """Finalise a completed rest, applying HP / slot / exhaustion effects.

    Returns ``None`` if the rest is not yet completed or was interrupted.
    """
    p = progress if progress is not None else getattr(actor, "rest_progress", None)
    if p is None or not p.completed or p.interrupted:
        return None
    if p.kind == "short":
        result = apply_short_rest(
            actor,
            hit_dice_spent=hit_dice_spent,
            roller=roller,
            con_modifier=con_modifier,
        )
    else:
        result = apply_long_rest(actor)
    if hasattr(actor, "rest_progress"):
        actor.rest_progress = None
    return result


def dawn_recharge(
    actor: Any, *, roller: DiceRoller | None = None
) -> dict[str, int]:
    """Drive every magic item with a dawn recharge on the actor's inventory."""
    inv = getattr(actor, "inventory", None)
    if inv is None or not hasattr(inv, "dawn_recharge"):
        return {}
    return inv.dawn_recharge(roller=roller)


__all__ = [
    "LONG_DURATION_HR",
    "LONG_MAX_PER_DAY",
    "RestKind",
    "RestProgress",
    "RestResult",
    "SHORT_DURATION_MIN",
    "apply_long_rest",
    "apply_short_rest",
    "begin_long_rest",
    "begin_short_rest",
    "complete_rest",
    "dawn_recharge",
]

