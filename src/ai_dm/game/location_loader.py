"""Loads scene-location asset files."""
from __future__ import annotations

import json
from pathlib import Path

from ai_dm.game.location_model import SceneLocation


def load_scene_locations(path: Path) -> list[SceneLocation]:
    """Read a single JSON file (object or list) into ``SceneLocation``s."""
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = [payload]
    return [SceneLocation.model_validate(item) for item in payload]


def load_directory(root: Path) -> list[SceneLocation]:
    """Recursively load every ``scene_locations.json`` under ``root``."""
    if not root.exists():
        return []
    out: list[SceneLocation] = []
    for path in root.rglob("scene_locations.json"):
        out.extend(load_scene_locations(path))
    return out

