"""How many in-game minutes a movement consumes.

The :class:`Clock` is minute-grained, so combat-round moves
(~30 ft / 6 s) round to zero. We surface a small helper that:

* Returns 0 for in-combat moves (the round structure handles
  sub-minute time elsewhere).
* Out of combat, charges at least 1 minute per move so casual
  exploration ("walk 30 ft toward the altar") visibly advances
  the clock.
* Otherwise scales by the SRD exploration pace
  (``ai_dm.rules.travel.pace``).

Travel between scenes uses :func:`travel_minutes` which falls back
to a 30-minute placeholder when no per-leg distance is known.
"""
from __future__ import annotations

import math

from ai_dm.rules.travel import Pace, distance_per_hour_mi, pace as _pace

# Default round trip per generic in-scene move when no distance is
# given — assume a single combat-round of movement (30 ft).
DEFAULT_MOVE_FT: int = 30
DEFAULT_TRAVEL_MIN: int = 30


def in_scene_move_minutes(
    distance_ft: int | None,
    *,
    in_combat: bool = False,
    pace: Pace = "normal",
) -> int:
    """Minutes consumed by an in-scene move of ``distance_ft``.

    Combat moves return 0 (the encounter loop tracks rounds in
    seconds outside the clock). Out of combat we always advance at
    least one minute so the player feels time pass.
    """
    if in_combat:
        return 0
    dist = int(distance_ft if distance_ft and distance_ft > 0 else DEFAULT_MOVE_FT)
    p = _pace(pace)
    minutes = math.ceil(dist / max(1, p.per_minute_ft))
    return max(1, int(minutes))


def travel_minutes(
    distance_mi: float | None = None,
    *,
    pace: Pace = "normal",
    mounted: bool = False,
    terrain: str = "normal",
) -> int:
    """Minutes consumed by a cross-scene travel leg.

    When the pack supplies ``distance_mi`` for the leg, scale by the
    chosen pace + terrain. Otherwise fall back to ``DEFAULT_TRAVEL_MIN``
    so the clock still ticks visibly per move.
    """
    if not distance_mi or distance_mi <= 0:
        return DEFAULT_TRAVEL_MIN
    mph = distance_per_hour_mi(pace, mounted=mounted, terrain=terrain)
    if mph <= 0:
        return DEFAULT_TRAVEL_MIN
    return max(1, int(math.ceil(float(distance_mi) / mph * 60)))


__all__ = [
    "DEFAULT_MOVE_FT",
    "DEFAULT_TRAVEL_MIN",
    "in_scene_move_minutes",
    "travel_minutes",
]

