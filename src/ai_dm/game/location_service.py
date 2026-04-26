"""Runtime service for anchor/zone resolution."""
from __future__ import annotations

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

    def snapshot(self) -> dict:
        with self._lock:
            return {sid: scene.model_dump() for sid, scene in self._scenes.items()}

    def restore(self, snapshot: dict) -> None:
        with self._lock:
            self._scenes = {
                sid: SceneLocation.model_validate(payload)
                for sid, payload in (snapshot or {}).items()
            }

