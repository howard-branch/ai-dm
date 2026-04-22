"""Pure data layer for in-scene anchors and zones.

Anchors are named (x, y) coordinates on a scene (``altar``, ``main_door``).
Zones are rectangular or polygonal regions used for "is the token in the
library?" queries.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Anchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    scene_id: str
    x: int
    y: int
    tags: list[str] = Field(default_factory=list)


class Zone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    scene_id: str
    shape: Literal["rect", "polygon"]
    rect: tuple[int, int, int, int] | None = None  # (x0, y0, x1, y1)
    polygon: list[tuple[int, int]] | None = None
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_shape(self) -> "Zone":
        if self.shape == "rect":
            if self.rect is None:
                raise ValueError("rect zone requires a `rect` tuple")
            x0, y0, x1, y1 = self.rect
            if x1 <= x0 or y1 <= y0:
                raise ValueError("rect must satisfy x1>x0 and y1>y0")
        else:
            if not self.polygon or len(self.polygon) < 3:
                raise ValueError("polygon zone needs at least 3 points")
        return self

    def contains(self, x: int, y: int) -> bool:
        if self.shape == "rect":
            assert self.rect is not None
            x0, y0, x1, y1 = self.rect
            return x0 <= x <= x1 and y0 <= y <= y1
        # Ray-casting algorithm.
        assert self.polygon is not None
        inside = False
        n = len(self.polygon)
        j = n - 1
        for i in range(n):
            xi, yi = self.polygon[i]
            xj, yj = self.polygon[j]
            if ((yi > y) != (yj > y)) and (
                x < (xj - xi) * (y - yi) / ((yj - yi) or 1) + xi
            ):
                inside = not inside
            j = i
        return inside

    def centroid(self) -> tuple[int, int]:
        if self.shape == "rect":
            assert self.rect is not None
            x0, y0, x1, y1 = self.rect
            return ((x0 + x1) // 2, (y0 + y1) // 2)
        assert self.polygon is not None
        xs = [p[0] for p in self.polygon]
        ys = [p[1] for p in self.polygon]
        return (sum(xs) // len(xs), sum(ys) // len(ys))


class SceneLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str
    anchors: list[Anchor] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)

