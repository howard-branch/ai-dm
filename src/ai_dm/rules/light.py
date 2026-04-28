"""Light, vision and obscurement.

Single source of truth: ``assets/srd5_2/core/light.json``.

* Templates → :class:`LightSource` (radii, duration, fuel).
* Vision helpers translate a list of overlapping light sources into the
  effective :data:`VisionLevel` at a given range; combine with
  ``has_darkvision`` to promote dim → bright per SRD.
* Obscurement helpers translate vision into perception/attack effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal

from ai_dm.rules.srd_core import load

VisionLevel = Literal["bright", "dim", "darkness"]
Obscurement = Literal["clear", "lightly", "heavily"]
AttackEffect = Literal["normal", "advantage", "disadvantage", "blocked"]
PerceptionEffect = Literal["normal", "disadvantage", "auto_fail"]


@dataclass(frozen=True)
class LightSource:
    key: str
    name: str
    bright_ft: int
    dim_ft: int
    duration_min: int | None
    consumable: bool
    fuel: str | None
    spell: bool
    shape: str


def _from_record(rec: dict[str, Any]) -> LightSource:
    dur = rec.get("duration_min")
    return LightSource(
        key=str(rec["key"]),
        name=str(rec["name"]),
        bright_ft=int(rec["bright_ft"]),
        dim_ft=int(rec["dim_ft"]),
        duration_min=int(dur) if dur is not None else None,
        consumable=bool(rec.get("consumable", False)),
        fuel=rec.get("fuel"),
        spell=bool(rec.get("spell", False)),
        shape=str(rec.get("shape", "radius")),
    )


_DATA = load("light")
_BY_KEY: dict[str, LightSource] = {
    r["key"]: _from_record(r) for r in _DATA["light_sources"]
}
VISION_LEVELS: tuple[VisionLevel, ...] = tuple(_DATA["vision_levels"])  # type: ignore[assignment]
OBSCUREMENT_LEVELS: tuple[Obscurement, ...] = tuple(_DATA["obscurement_levels"])  # type: ignore[assignment]
DARKVISION_DIM_TO_BRIGHT: bool = bool(_DATA.get("darkvision_dim_to_bright", True))


def get_light(key: str) -> LightSource | None:
    return _BY_KEY.get(key)


def is_light(key: str) -> bool:
    return key in _BY_KEY


def all_lights() -> list[LightSource]:
    return list(_BY_KEY.values())


# --------------------------------------------------------------------- #
# Vision resolution
# --------------------------------------------------------------------- #


def vision_at(distance_ft: float, source: LightSource) -> VisionLevel:
    """Vision level produced by ``source`` at ``distance_ft``."""
    d = float(distance_ft)
    if d <= source.bright_ft:
        return "bright"
    if d <= source.dim_ft:
        return "dim"
    return "darkness"


_RANK = {"bright": 2, "dim": 1, "darkness": 0}


def _max_level(*levels: VisionLevel) -> VisionLevel:
    out: VisionLevel = "darkness"
    for lv in levels:
        if _RANK[lv] > _RANK[out]:
            out = lv
    return out


def effective_vision(
    distances: Iterable[tuple[float, LightSource]],
    *,
    ambient: VisionLevel = "darkness",
    has_darkvision: bool = False,
) -> VisionLevel:
    """Pick the brightest vision level produced by overlapping sources.

    ``has_darkvision`` promotes a final ``dim`` (or "dim from ambient")
    one step toward ``bright`` per SRD; in pure darkness darkvision is
    treated as dim by the caller (per the SRD it sees as if dim out
    to its range).
    """
    best: VisionLevel = ambient
    for dist, src in distances:
        best = _max_level(best, vision_at(dist, src))
    if has_darkvision and DARKVISION_DIM_TO_BRIGHT and best == "dim":
        return "bright"
    return best


def obscurement_for(vision: VisionLevel) -> Obscurement:
    """Translate vision into the obscurement category."""
    if vision == "bright":
        return "clear"
    if vision == "dim":
        return "lightly"
    return "heavily"


def attack_modifier(
    attacker_vision: VisionLevel,
    target_obscurement: Obscurement,
    *,
    has_darkvision: bool = False,
) -> AttackEffect:
    """Per SRD: heavy obscurement / blindness → blocked (counts as
    blinded for attack purposes); lightly obscured → no penalty (we
    return ``normal``); attacker in dim or darkness without sight →
    disadvantage. Target unseen and attacker can't see → blocked.
    """
    if target_obscurement == "heavily":
        return "blocked"
    if attacker_vision == "darkness" and not has_darkvision:
        return "blocked"
    if attacker_vision == "dim" and not has_darkvision:
        return "disadvantage"
    return "normal"


def perception_modifier(obs: Obscurement) -> PerceptionEffect:
    if obs == "lightly":
        return "disadvantage"
    if obs == "heavily":
        return "auto_fail"
    return "normal"


__all__ = [
    "AttackEffect",
    "DARKVISION_DIM_TO_BRIGHT",
    "LightSource",
    "OBSCUREMENT_LEVELS",
    "Obscurement",
    "PerceptionEffect",
    "VISION_LEVELS",
    "VisionLevel",
    "all_lights",
    "attack_modifier",
    "effective_vision",
    "get_light",
    "is_light",
    "obscurement_for",
    "perception_modifier",
    "vision_at",
]

