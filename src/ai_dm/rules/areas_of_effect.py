"""Areas of effect — SRD 5.2.

Single source of truth: ``assets/srd5_2/core/areas_of_effect.json``.

Pure-geometry helpers: each shape exposes a ``points_in_<shape>``
predicate so callers can fan out an AoE over a list of candidate
positions. All distances are in feet; the caller is responsible for
unit conversion if its candidates carry pixel coordinates.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Literal

from ai_dm.rules.srd_core import load

_DATA = load("areas_of_effect")
ShapeKey = Literal["sphere", "cube", "cone", "line", "cylinder"]
SHAPES: tuple[ShapeKey, ...] = tuple(s["key"] for s in _DATA["shapes"])  # type: ignore[assignment]
_SHAPE_DEFAULTS: dict[str, dict] = {s["key"]: dict(s) for s in _DATA["shapes"]}
LINE_DEFAULT_WIDTH_FT: int = int(_SHAPE_DEFAULTS["line"].get("width_ft", 5))


@dataclass
class AreaTemplate:
    shape: ShapeKey
    anchor: tuple[float, float]
    size_ft: float
    direction_deg: float = 0.0
    width_ft: float | None = None  # for line; defaults to LINE_DEFAULT_WIDTH_FT
    height_ft: float | None = None  # for cylinder

    def to_dict(self) -> dict[str, Any]:
        return {
            "shape": self.shape,
            "anchor": {"x": self.anchor[0], "y": self.anchor[1]},
            "size_ft": self.size_ft,
            "direction_deg": self.direction_deg,
            "width_ft": self.width_ft,
            "height_ft": self.height_ft,
        }


def points_in_sphere(
    points: Iterable[tuple[float, float]],
    *,
    center: tuple[float, float],
    radius_ft: float,
) -> list[tuple[float, float]]:
    cx, cy = center
    return [p for p in points if math.hypot(p[0] - cx, p[1] - cy) <= radius_ft]


def points_in_cube(
    points: Iterable[tuple[float, float]],
    *,
    origin: tuple[float, float],
    side_ft: float,
) -> list[tuple[float, float]]:
    """Cube anchored with ``origin`` at one corner."""
    ox, oy = origin
    return [
        p for p in points
        if ox <= p[0] <= ox + side_ft and oy <= p[1] <= oy + side_ft
    ]


def points_in_cone(
    points: Iterable[tuple[float, float]],
    *,
    apex: tuple[float, float],
    length_ft: float,
    direction_deg: float,
    half_angle_deg: float = 26.565,  # 5e default ≈ atan(0.5)
) -> list[tuple[float, float]]:
    """5e cone: equal length and width at terminus → half-angle ≈ 26.5°."""
    ax, ay = apex
    dirx = math.cos(math.radians(direction_deg))
    diry = math.sin(math.radians(direction_deg))
    out: list[tuple[float, float]] = []
    cos_thresh = math.cos(math.radians(half_angle_deg))
    for p in points:
        dx, dy = p[0] - ax, p[1] - ay
        dist = math.hypot(dx, dy)
        if dist <= 0:
            out.append(p)
            continue
        if dist > length_ft:
            continue
        # Cosine of angle between (dx,dy) and direction.
        if (dx * dirx + dy * diry) / dist >= cos_thresh:
            out.append(p)
    return out


def points_in_line(
    points: Iterable[tuple[float, float]],
    *,
    origin: tuple[float, float],
    length_ft: float,
    direction_deg: float,
    width_ft: float | None = None,
) -> list[tuple[float, float]]:
    if width_ft is None:
        width_ft = LINE_DEFAULT_WIDTH_FT
    ox, oy = origin
    half_w = float(width_ft) / 2.0
    dirx = math.cos(math.radians(direction_deg))
    diry = math.sin(math.radians(direction_deg))
    # Perpendicular unit vector.
    perpx, perpy = -diry, dirx
    out: list[tuple[float, float]] = []
    for p in points:
        dx, dy = p[0] - ox, p[1] - oy
        along = dx * dirx + dy * diry
        across = abs(dx * perpx + dy * perpy)
        if 0.0 <= along <= length_ft and across <= half_w:
            out.append(p)
    return out


def points_in_template(
    template: AreaTemplate,
    points: Iterable[tuple[float, float]],
) -> list[tuple[float, float]]:
    pts = list(points)
    if template.shape == "sphere" or template.shape == "cylinder":
        return points_in_sphere(pts, center=template.anchor, radius_ft=template.size_ft)
    if template.shape == "cube":
        return points_in_cube(pts, origin=template.anchor, side_ft=template.size_ft)
    if template.shape == "cone":
        return points_in_cone(
            pts, apex=template.anchor, length_ft=template.size_ft,
            direction_deg=template.direction_deg,
        )
    if template.shape == "line":
        return points_in_line(
            pts, origin=template.anchor, length_ft=template.size_ft,
            direction_deg=template.direction_deg,
            width_ft=template.width_ft,
        )
    raise ValueError(f"unsupported shape {template.shape!r}")


__all__ = [
    "AreaTemplate",
    "LINE_DEFAULT_WIDTH_FT",
    "SHAPES",
    "ShapeKey",
    "points_in_cone",
    "points_in_cube",
    "points_in_line",
    "points_in_sphere",
    "points_in_template",
]

