"""Reusable helpers for projecting a campaign-pack scene into Foundry.

These were previously private helpers in :mod:`ai_dm.app.bootstrap` used
only by the startup sequence. Travel between scenes (see
``IntentRouter._dispatch_travel``) needs the same logic — otherwise
``activate_scene`` is dispatched for a scene that was never created in
Foundry, the registry lookup misses, and the validator rejects the
command with ``unknown_scene``.

Two functions are exposed:

* :func:`build_create_scene` — sized to encompass every authored anchor
  / zone in ``locations/*/scene_locations.json`` for the scene.
* :func:`build_anchor_pin_commands` — idempotent ``create_note``
  commands so map-based moves like ``move to brink`` resolve via
  Foundry's ``findTargetOnScene``.

Both return concrete :class:`GameCommand` instances that can be passed
straight to :meth:`BatchExecutor.execute`.
"""
from __future__ import annotations

import logging
from typing import Any

from ai_dm.models.commands import CreateNoteCommand, CreateSceneCommand

logger = logging.getLogger("ai_dm.app.scene_setup")

_MIN_SCENE_DIM = 1500
_DEFAULT_SCENE_DIM = 4000
_SCENE_PAD = 400
_GRID_SIZE = 100


def _scene_bounds(location_service: Any, scene_id: str) -> tuple[int, int] | None:
    if location_service is None or not scene_id:
        return None
    scene = location_service.get_scene(scene_id)
    if scene is None:
        return None
    xs: list[int] = []
    ys: list[int] = []
    for a in (scene.anchors or []):
        xs.append(int(a.x)); ys.append(int(a.y))
    for z in (scene.zones or []):
        if z.shape == "rect" and z.rect:
            x0, y0, x1, y1 = z.rect
            xs.extend([int(x0), int(x1)])
            ys.extend([int(y0), int(y1)])
        elif z.polygon:
            for px, py in z.polygon:
                xs.append(int(px)); ys.append(int(py))
    if not xs or not ys:
        return None
    width = max(_MIN_SCENE_DIM, max(xs) + _SCENE_PAD)
    height = max(_MIN_SCENE_DIM, max(ys) + _SCENE_PAD)
    width = ((width + _GRID_SIZE - 1) // _GRID_SIZE) * _GRID_SIZE
    height = ((height + _GRID_SIZE - 1) // _GRID_SIZE) * _GRID_SIZE
    return (width, height)


def build_create_scene(location_service: Any, scene_id: str) -> CreateSceneCommand:
    """Construct a :class:`CreateSceneCommand` sized to the pack's anchors."""
    bounds = _scene_bounds(location_service, scene_id)
    if bounds is None:
        return CreateSceneCommand(name=scene_id)
    w, h = bounds
    logger.info(
        "scene %s sized to %dx%d (grid=%d) from pack anchors/zones",
        scene_id, w, h, _GRID_SIZE,
    )
    return CreateSceneCommand(name=scene_id, width=w, height=h, grid=_GRID_SIZE)


def build_anchor_pin_commands(location_service: Any, scene_id: str) -> list[CreateNoteCommand]:
    """Build idempotent ``create_note`` commands for every anchor on
    ``scene_id``. Returns an empty list when no anchors are authored.
    """
    out: list[CreateNoteCommand] = []
    if location_service is None or not scene_id:
        return out
    scene = location_service.get_scene(scene_id)
    if scene is None:
        return out
    seen: set[str] = set()
    for anchor in (scene.anchors or []):
        name = (anchor.name or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            out.append(
                CreateNoteCommand(
                    scene_id=scene_id,
                    x=int(anchor.x),
                    y=int(anchor.y),
                    text=name,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("anchor pin skipped (%s): %s", name, exc)
    if out:
        logger.info(
            "projecting %d anchor pin(s) onto scene %s: %s",
            len(out), scene_id, [c.text for c in out],
        )
    return out

