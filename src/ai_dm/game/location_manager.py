"""Backward-compatible location façade.

Historically returned a hard-coded ``"candlekeep_gate"``. The runtime
truth lives on :class:`ai_dm.ai.planner.StoryPlanner` (``state.current_scene``)
and falls back to the active campaign pack's ``manifest.start.scene``.
"""
from __future__ import annotations

from typing import Any

from ai_dm.game.location_service import LocationService


class LocationManager:
    """Thin façade kept for backward compatibility."""

    def __init__(
        self,
        service: LocationService | None = None,
        *,
        story_planner: Any | None = None,
        pack: Any | None = None,
    ) -> None:
        self.service = service or LocationService()
        self.story_planner = story_planner
        self.pack = pack

    def current_location(self) -> str | None:
        """Return the active scene id, or ``None`` if unknown.

        Resolution order:

        1. ``StoryPlanner.state.current_scene`` (set by ``enter_scene``).
        2. The active campaign pack's ``manifest.start.scene``.
        """
        planner = self.story_planner
        if planner is not None:
            try:
                sid = getattr(getattr(planner, "state", None), "current_scene", None)
                if sid:
                    return sid
            except Exception:  # noqa: BLE001
                pass

        pack = self.pack
        if pack is not None:
            try:
                start = getattr(getattr(pack, "manifest", None), "start", None)
                sid = getattr(start, "scene", None) if start is not None else None
                if sid:
                    return sid
            except Exception:  # noqa: BLE001
                pass

        return None
