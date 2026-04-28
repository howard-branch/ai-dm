"""Runtime service for anchor/zone resolution."""
from __future__ import annotations

import math
import threading
from typing import Iterable

from ai_dm.foundry.errors import RegistryMissError
from ai_dm.foundry.registry import FoundryRegistry
from ai_dm.game.location_model import Anchor, SceneLocation, Zone


class LocationService:
    def __init__(self, registry: FoundryRegistry | None = None) -> None:
        self.registry = registry
        self._lock = threading.RLock()
        self._scenes: dict[str, SceneLocation] = {}

    # ------------------------------------------------------------------ #

    def load_scene(self, scene: SceneLocation) -> None:
        with self._lock:
            self._scenes[scene.scene_id] = scene

    def load_many(self, scenes: Iterable[SceneLocation]) -> None:
        for s in scenes:
            self.load_scene(s)

    def get_scene(self, scene_id: str) -> SceneLocation | None:
        with self._lock:
            return self._scenes.get(scene_id)

    # ------------------------------------------------------------------ #

    def resolve_anchor(self, scene_id: str, name_or_id: str) -> tuple[int, int]:
        scene = self.get_scene(scene_id)
        if scene is None:
            raise RegistryMissError("anchor", name_or_id, scene_id)
        norm = name_or_id.strip().lower()
        for anchor in scene.anchors:
            if anchor.id == name_or_id or anchor.name.lower() == norm:
                return (anchor.x, anchor.y)
        raise RegistryMissError("anchor", name_or_id, scene_id)

    def resolve_anywhere(
        self, name_or_id: str
    ) -> tuple[str, int, int] | None:
        """Search **every** loaded scene for an anchor with this name/id.

        Used as a fall-back when ``resolve_anchor`` misses because the
        caller's ``scene_id`` doesn't match the campaign-pack scene id
        (e.g. a Foundry scene id was passed in instead of the slug).
        Returns ``(scene_id, x, y)`` of the first hit, or ``None``.
        """
        if not name_or_id:
            return None
        norm = name_or_id.strip().lower()
        with self._lock:
            for sid, scene in self._scenes.items():
                for anchor in scene.anchors:
                    if anchor.id == name_or_id or anchor.name.lower() == norm:
                        return (sid, anchor.x, anchor.y)
        return None

    def get_anchor(self, scene_id: str, name_or_id: str) -> Anchor | None:
        try:
            self.resolve_anchor(scene_id, name_or_id)
        except RegistryMissError:
            return None
        # Re-lookup to return the full object.
        scene = self.get_scene(scene_id)
        norm = name_or_id.strip().lower()
        for anchor in scene.anchors:  # type: ignore[union-attr]
            if anchor.id == name_or_id or anchor.name.lower() == norm:
                return anchor
        return None

    def zones_at(self, scene_id: str, x: int, y: int) -> list[Zone]:
        scene = self.get_scene(scene_id)
        if scene is None:
            return []
        return [z for z in scene.zones if z.contains(x, y)]

    # ------------------------------------------------------------------ #
    # Geometry: partial moves and formation slots
    # ------------------------------------------------------------------ #

    def pixels_per_foot(self, scene_id: str | None) -> float:
        """Return px/ft for ``scene_id`` (defaults to 20 if unknown:
        100px/5ft, the Foundry/SRD default)."""
        scene = self.get_scene(scene_id) if scene_id else None
        if scene is None:
            return 100.0 / 5.0  # safe default
        return scene.pixels_per_foot()

    def grid_size_px(self, scene_id: str | None) -> int:
        scene = self.get_scene(scene_id) if scene_id else None
        return int((scene.grid_size_px if scene else 100) or 100)

    def point_along(
        self,
        scene_id: str | None,
        from_xy: tuple[float, float],
        to_xy: tuple[float, float],
        distance_ft: float,
        *,
        snap: bool = True,
    ) -> tuple[int, int]:
        """Return a point ``distance_ft`` from ``from_xy`` toward
        ``to_xy``, clamped to the segment endpoint. Snaps to the
        scene's grid when ``snap=True``.
        """
        fx, fy = float(from_xy[0]), float(from_xy[1])
        tx, ty = float(to_xy[0]), float(to_xy[1])
        dx, dy = tx - fx, ty - fy
        seg_px = math.hypot(dx, dy)
        if seg_px <= 1e-6 or distance_ft <= 0:
            return (int(round(fx)), int(round(fy)))
        ppf = self.pixels_per_foot(scene_id)
        want_px = float(distance_ft) * ppf
        t = min(1.0, want_px / seg_px)
        x = fx + dx * t
        y = fy + dy * t
        if snap:
            g = self.grid_size_px(scene_id)
            if g > 0:
                x = round(x / g) * g
                y = round(y / g) * g
        return (int(round(x)), int(round(y)))

    def project_direction(
        self,
        scene_id: str | None,
        from_xy: tuple[float, float],
        direction: str,
        distance_ft: float,
        *,
        snap: bool = True,
    ) -> tuple[int, int]:
        """Project a cardinal-direction move from ``from_xy``.

        Foundry y increases downward, so "north" subtracts y. Diagonal
        directions use unit-length components (no √2 distance penalty
        — matches 5e's "every diagonal counts as 5 ft" simple rule).
        """
        d = (direction or "").strip().lower()
        comp = {
            "north": (0, -1),  "n": (0, -1),
            "south": (0, 1),   "s": (0, 1),
            "east":  (1, 0),   "e": (1, 0),
            "west":  (-1, 0),  "w": (-1, 0),
            "northeast": (1, -1),  "ne": (1, -1),
            "northwest": (-1, -1), "nw": (-1, -1),
            "southeast": (1, 1),   "se": (1, 1),
            "southwest": (-1, 1),  "sw": (-1, 1),
            "up": (0, -1), "down": (0, 1),
            "left": (-1, 0), "right": (1, 0),
        }.get(d)
        if comp is None:
            return (int(round(from_xy[0])), int(round(from_xy[1])))
        ux, uy = comp
        ppf = self.pixels_per_foot(scene_id)
        x = float(from_xy[0]) + ux * float(distance_ft) * ppf
        y = float(from_xy[1]) + uy * float(distance_ft) * ppf
        if snap:
            g = self.grid_size_px(scene_id)
            if g > 0:
                x = round(x / g) * g
                y = round(y / g) * g
        return (int(round(x)), int(round(y)))

    @staticmethod
    def formation_offsets(count: int) -> list[tuple[int, int]]:
        """Return ``count`` distinct (dx, dy) offsets in *grid cells*
        suitable for laying out a party of ``count`` tokens around a
        destination. Index 0 is the centre (the lead PC), the rest
        spiral outward in a ring pattern so no two slots collide.
        """
        if count <= 0:
            return []
        # Hand-tuned ring expansion: centre, then the 8 neighbours,
        # then the 16 next-ring cells. Covers parties up to 25 with
        # no collisions; beyond that we fall back to a generated
        # ring further out.
        ring = [
            (0, 0),
            (1, 0), (-1, 0), (0, 1), (0, -1),
            (1, 1), (-1, 1), (1, -1), (-1, -1),
            (2, 0), (-2, 0), (0, 2), (0, -2),
            (2, 1), (-2, 1), (2, -1), (-2, -1),
            (1, 2), (-1, 2), (1, -2), (-1, -2),
            (2, 2), (-2, 2), (2, -2), (-2, -2),
        ]
        out = list(ring[: count])
        # Generate further rings if a freakishly-large party shows up.
        r = 3
        while len(out) < count:
            for i in range(-r, r + 1):
                for j in range(-r, r + 1):
                    if abs(i) == r or abs(j) == r:
                        out.append((i, j))
                        if len(out) >= count:
                            break
                if len(out) >= count:
                    break
            r += 1
        return out[:count]

    def formation_slots(
        self,
        scene_id: str | None,
        center_xy: tuple[int, int],
        count: int,
    ) -> list[tuple[int, int]]:
        """Return ``count`` grid-snapped (x, y) slots clustered around
        ``center_xy``. The lead actor goes first; the rest occupy the
        surrounding ring so the party doesn't stack on a single tile.
        """
        if count <= 0:
            return []
        g = self.grid_size_px(scene_id)
        cx, cy = int(center_xy[0]), int(center_xy[1])
        # Snap centre to grid so neighbours align cleanly.
        if g > 0:
            cx = round(cx / g) * g
            cy = round(cy / g) * g
        slots: list[tuple[int, int]] = []
        for dx, dy in self.formation_offsets(count):
            slots.append((cx + dx * g, cy + dy * g))
        return slots

    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict:
        with self._lock:
            return {sid: scene.model_dump() for sid, scene in self._scenes.items()}

    def restore(self, snapshot: dict) -> None:
        with self._lock:
            self._scenes = {
                sid: SceneLocation.model_validate(payload)
                for sid, payload in (snapshot or {}).items()
            }

