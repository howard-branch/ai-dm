"""Travel pace, overland distance and forced-march rules.

Single source of truth: ``assets/srd5_2/core/travel.json``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai_dm.rules.srd_core import load

Pace = Literal["slow", "normal", "fast"]
PACES: tuple[Pace, ...] = ("slow", "normal", "fast")


@dataclass(frozen=True)
class PaceProfile:
    name: Pace
    per_minute_ft: int
    per_hour_mi: int
    per_day_mi: int
    stealth_ok: bool
    perception_penalty: int
    navigation_dc_mod: int


@dataclass(frozen=True)
class ForcedMarchRule:
    hours_before_check: int
    save_dc_base: int
    dc_per_extra_hour: int
    save_ability: str
    exhaustion_on_fail: int


_DATA = load("travel")
_PACES: dict[str, PaceProfile] = {
    name: PaceProfile(
        name=name,  # type: ignore[arg-type]
        per_minute_ft=int(row["per_minute_ft"]),
        per_hour_mi=int(row["per_hour_mi"]),
        per_day_mi=int(row["per_day_mi"]),
        stealth_ok=bool(row["stealth_ok"]),
        perception_penalty=int(row["perception_penalty"]),
        navigation_dc_mod=int(row["navigation_dc_mod"]),
    )
    for name, row in _DATA["paces"].items()
}
_FORCED = _DATA["forced_march"]
FORCED_MARCH = ForcedMarchRule(
    hours_before_check=int(_FORCED["hours_before_check"]),
    save_dc_base=int(_FORCED["save_dc_base"]),
    dc_per_extra_hour=int(_FORCED["dc_per_extra_hour"]),
    save_ability=str(_FORCED["save_ability"]),
    exhaustion_on_fail=int(_FORCED["exhaustion_on_fail"]),
)
TERRAIN_MULTIPLIERS: dict[str, float] = {
    str(k): float(v) for k, v in _DATA["terrain_multipliers"].items()
}
MOUNT_SPEED_FACTOR: float = float(_DATA.get("mount_speed_factor", 2.0))
DEFAULT_TRAVEL_HOURS_PER_DAY: int = int(_DATA.get("default_travel_hours_per_day", 8))


def pace(name: Pace) -> PaceProfile:
    return _PACES[name]


def all_paces() -> list[PaceProfile]:
    return [_PACES[p] for p in PACES]


def distance_per_hour_mi(
    p: Pace, *, mounted: bool = False, terrain: str = "normal"
) -> float:
    base = _PACES[p].per_hour_mi
    factor = TERRAIN_MULTIPLIERS.get(terrain, 1.0)
    if mounted:
        factor *= MOUNT_SPEED_FACTOR
    return round(base * factor, 4)


def distance_per_day_mi(
    p: Pace,
    *,
    mounted: bool = False,
    hours: int | None = None,
    terrain: str = "normal",
) -> float:
    if hours is None:
        # Apply terrain to the SRD canonical 8h/day figure.
        base = _PACES[p].per_day_mi
        factor = TERRAIN_MULTIPLIERS.get(terrain, 1.0)
        if mounted:
            factor *= MOUNT_SPEED_FACTOR
        return round(base * factor, 4)
    return round(distance_per_hour_mi(p, mounted=mounted, terrain=terrain) * hours, 4)


def perception_penalty(p: Pace) -> int:
    return _PACES[p].perception_penalty


def can_stealth(p: Pace) -> bool:
    return _PACES[p].stealth_ok


def navigation_dc(base_dc: int, p: Pace) -> int:
    return int(base_dc) + _PACES[p].navigation_dc_mod


def forced_march_save_dc(hours_marched: int) -> int | None:
    """DC for the end-of-hour Con save, or ``None`` under threshold."""
    extra = int(hours_marched) - FORCED_MARCH.hours_before_check
    if extra <= 0:
        return None
    return FORCED_MARCH.save_dc_base + FORCED_MARCH.dc_per_extra_hour * extra


__all__ = [
    "DEFAULT_TRAVEL_HOURS_PER_DAY",
    "FORCED_MARCH",
    "ForcedMarchRule",
    "MOUNT_SPEED_FACTOR",
    "PACES",
    "Pace",
    "PaceProfile",
    "TERRAIN_MULTIPLIERS",
    "all_paces",
    "can_stealth",
    "distance_per_day_mi",
    "distance_per_hour_mi",
    "forced_march_save_dc",
    "navigation_dc",
    "pace",
    "perception_penalty",
]

