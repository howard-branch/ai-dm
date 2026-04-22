"""Settings loader.

Reads ``config/settings.yaml`` (if present) and exposes a few typed
helpers — primarily for resolving the active campaign pack.

Today only a small subset of the YAML is consumed; the rest of the
``Container`` still relies on dataclass defaults. New code should reach
for these helpers rather than re-parsing the YAML.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai_dm.app.settings")

DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")


@dataclass(frozen=True)
class CampaignsSettings:
    root: Path = Path("campaigns")
    active: str | None = None
    state_root: Path = Path("data/campaigns")


@dataclass(frozen=True)
class Settings:
    raw: dict[str, Any]
    campaigns: CampaignsSettings

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or DEFAULT_SETTINGS_PATH
        data: dict[str, Any] = {}
        if path.exists():
            try:
                import yaml  # type: ignore[import-not-found]

                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception as exc:  # noqa: BLE001
                logger.warning("settings.yaml unreadable: %s", exc)
                data = {}
        c = (data.get("campaigns") or {})
        return cls(
            raw=data,
            campaigns=CampaignsSettings(
                root=Path(c.get("root") or "campaigns"),
                active=(c.get("active") or None),
                state_root=Path(c.get("state_root") or "data/campaigns"),
            ),
        )

