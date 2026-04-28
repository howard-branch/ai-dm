"""Movement — SRD 5.2.

Single source of truth: ``assets/srd5_2/core/movement.json``.

Movement is measured in feet; geometry concerns (pixel coords, grid
sizes, diagonals) live in :mod:`ai_dm.rules.targeting` /
``geometry``. This module is a pure budget tracker.
"""
from __future__ import annotations

from typing import Any

from ai_dm.rules.srd_core import load

_DATA = load("movement")
DEFAULT_SPEED_FT: int = int(_DATA["default_speed_ft"])
MODES: tuple[str, ...] = tuple(_DATA["modes"])
DIFFICULT_TERRAIN_FACTOR: int = int(_DATA["difficult_terrain_factor"])
PRONE_CRAWL_FACTOR: int = int(_DATA["prone_crawl_factor"])
CLIMBING_FACTOR: int = int(_DATA["climbing_factor"])
SWIMMING_FACTOR: int = int(_DATA["swimming_factor"])
STANDING_COSTS_HALF_SPEED: bool = bool(_DATA["standing_costs_half_speed"])


def cost(
    distance_ft: int,
    *,
    difficult: bool = False,
    climbing: bool = False,
    swimming: bool = False,
    crawling: bool = False,
) -> int:
    """Effective movement cost in feet for moving ``distance_ft``."""
    factor = 1
    if difficult:
        factor *= DIFFICULT_TERRAIN_FACTOR
    if climbing:
        factor *= CLIMBING_FACTOR
    if swimming:
        factor *= SWIMMING_FACTOR
    if crawling:
        factor *= PRONE_CRAWL_FACTOR
    return int(distance_ft) * factor


def budget(actor: Any) -> int:
    """Total speed budget this turn (after dash + exhaustion penalty)."""
    base = int(getattr(actor, "speed", DEFAULT_SPEED_FT) or DEFAULT_SPEED_FT)
    if getattr(actor, "dashed", False):
        base *= 2
    # Exhaustion speed penalty (per level of exhaustion).
    try:
        from ai_dm.rules.exhaustion import speed_penalty_ft
        base += int(speed_penalty_ft(int(getattr(actor, "exhaustion", 0) or 0)))
    except Exception:  # noqa: BLE001
        pass
    return max(0, base)


def remaining(actor: Any) -> int:
    return max(0, budget(actor) - int(getattr(actor, "movement_used", 0) or 0))


def can_move(actor: Any, distance_ft: int, **opts: Any) -> bool:
    return cost(distance_ft, **opts) <= remaining(actor)


def spend(actor: Any, distance_ft: int, **opts: Any) -> int:
    """Spend ``distance_ft`` from the actor's budget; raise on overspend.

    Returns the remaining budget after spending.
    """
    needed = cost(distance_ft, **opts)
    rem = remaining(actor)
    if needed > rem:
        raise ValueError(
            f"insufficient movement: need {needed} ft, have {rem} ft"
        )
    actor.movement_used = int(getattr(actor, "movement_used", 0) or 0) + needed
    return remaining(actor)


def stand_up_cost(actor: Any) -> int:
    """Cost to stand from prone (half base speed, ignoring dash bonus)."""
    base = int(getattr(actor, "speed", DEFAULT_SPEED_FT) or DEFAULT_SPEED_FT)
    return base // 2 if STANDING_COSTS_HALF_SPEED else 0


__all__ = [
    "CLIMBING_FACTOR",
    "DEFAULT_SPEED_FT",
    "DIFFICULT_TERRAIN_FACTOR",
    "MODES",
    "PRONE_CRAWL_FACTOR",
    "STANDING_COSTS_HALF_SPEED",
    "SWIMMING_FACTOR",
    "budget",
    "can_move",
    "cost",
    "remaining",
    "spend",
    "stand_up_cost",
]

