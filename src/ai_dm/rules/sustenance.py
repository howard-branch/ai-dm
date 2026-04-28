"""Food / water / starvation / dehydration rules.

Single source of truth: ``assets/srd5_2/core/sustenance.json``.

Per-actor runtime state lives on
:class:`ai_dm.game.combatant_state.CombatantState.nourishment`.
"""
from __future__ import annotations

from typing import Any

from ai_dm.rules import exhaustion as exh
from ai_dm.rules.dice import DiceRoller
from ai_dm.rules.srd_core import load

_DATA = load("sustenance")
_RATIONS_LB: dict[str, float] = {
    str(k): float(v) for k, v in _DATA["rations_per_day_lb_by_size"].items()
}
_WATER_GAL: dict[str, float] = {
    str(k): float(v) for k, v in _DATA["water_per_day_gal_by_size"].items()
}
_STARV = _DATA["starvation"]
_DEHY = _DATA["dehydration"]
_REST = _DATA["rest"]

MIN_GRACE_DAYS: int = int(_STARV["min_grace_days"])
GRACE_PER_CON_MOD: int = int(_STARV["days_grace_per_con_mod"])
EXH_PER_DAY_AFTER: int = int(_STARV["exhaustion_per_day_after"])

DEHY_HOT_DOUBLES: bool = bool(_DEHY["hot_climate_doubles"])
DEHY_DC_BASE: int = int(_DEHY["save_dc_base"])
DEHY_DC_PER_PREV: int = int(_DEHY["save_dc_per_prev_failed_day"])
DEHY_EXH_ON_FAIL: int = int(_DEHY["exhaustion_on_fail"])
DEHY_ABILITY: str = str(_DEHY["save_ability"])

SHORT_REST_MIN_MINUTES: int = int(_REST["short_min_minutes"])
LONG_REST_MIN_HOURS: int = int(_REST["long_min_hours"])
LONG_REST_MAX_PER_24H: int = int(_REST["long_max_per_24h"])
INTERRUPT_LONG_STRENUOUS_MIN: int = int(_REST["interrupt_long_strenuous_minutes"])
INTERRUPT_SHORT_COMBAT_ROUNDS: int = int(_REST["interrupt_short_combat_rounds"])


def rations_per_day_lb(size: str) -> float:
    """Pounds of food a creature of ``size`` needs per day."""
    return _RATIONS_LB.get(str(size).lower(), _RATIONS_LB["medium"])


def water_per_day_gal(size: str, *, hot_climate: bool = False) -> float:
    """Gallons of water a creature of ``size`` needs per day (doubles in hot weather)."""
    base = _WATER_GAL.get(str(size).lower(), _WATER_GAL["medium"])
    if hot_climate and DEHY_HOT_DOUBLES:
        base *= 2
    return base


def starvation_grace_days(con_modifier: int) -> int:
    """Days a creature can go without food before exhaustion ticks (SRD)."""
    return max(MIN_GRACE_DAYS, MIN_GRACE_DAYS + GRACE_PER_CON_MOD * int(con_modifier))


def tick_starvation(actor: Any, *, days_without_food: int) -> int:
    """Apply 1 level of exhaustion if past the actor's grace days.

    Returns the new exhaustion level.
    """
    con = int((getattr(actor, "ability_mods", {}) or {}).get("con", 0))
    grace = starvation_grace_days(con)
    cur = int(getattr(actor, "exhaustion", 0) or 0)
    if days_without_food > grace:
        cur = exh.add(cur, EXH_PER_DAY_AFTER)
        if hasattr(actor, "exhaustion"):
            actor.exhaustion = cur
    return cur


def dehydration_dc(prev_failed_days: int) -> int:
    """Con-save DC for a missed-water day."""
    return DEHY_DC_BASE + DEHY_DC_PER_PREV * max(0, int(prev_failed_days))


def tick_dehydration(
    actor: Any,
    *,
    prev_failed_days: int = 0,
    save_total: int | None = None,
    roller: DiceRoller | None = None,
) -> tuple[int, bool]:
    """Roll (or accept) a Con save against dehydration.

    Returns ``(new_exhaustion, succeeded)``. On failure adds 1 level of
    exhaustion. ``save_total`` lets callers supply a pre-rolled total
    (e.g. from a chat command); otherwise we roll using the actor's
    Con save modifier.
    """
    dc = dehydration_dc(prev_failed_days)
    if save_total is None:
        r = roller or DiceRoller(seed=0)
        mod = int((getattr(actor, "saving_throws", {}) or {}).get(DEHY_ABILITY, 0))
        save_total = r.roll("1d20").total + mod
    succeeded = save_total >= dc
    cur = int(getattr(actor, "exhaustion", 0) or 0)
    if not succeeded:
        cur = exh.add(cur, DEHY_EXH_ON_FAIL)
        if hasattr(actor, "exhaustion"):
            actor.exhaustion = cur
    return cur, succeeded


__all__ = [
    "DEHY_ABILITY",
    "DEHY_DC_BASE",
    "DEHY_DC_PER_PREV",
    "DEHY_EXH_ON_FAIL",
    "DEHY_HOT_DOUBLES",
    "EXH_PER_DAY_AFTER",
    "GRACE_PER_CON_MOD",
    "INTERRUPT_LONG_STRENUOUS_MIN",
    "INTERRUPT_SHORT_COMBAT_ROUNDS",
    "LONG_REST_MAX_PER_24H",
    "LONG_REST_MIN_HOURS",
    "MIN_GRACE_DAYS",
    "SHORT_REST_MIN_MINUTES",
    "dehydration_dc",
    "rations_per_day_lb",
    "starvation_grace_days",
    "tick_dehydration",
    "tick_starvation",
    "water_per_day_gal",
]

