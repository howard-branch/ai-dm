"""Settings loader.

Reads ``config/settings.yaml`` (if present) and exposes a few typed
helpers — primarily for resolving the active campaign pack.

Today only a small subset of the YAML is consumed; the rest of the
``Container`` still relies on dataclass defaults. New code should reach
for these helpers rather than re-parsing the YAML.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai_dm.app.settings")

DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
_REPO_MARKERS = ("pyproject.toml", ".git")


def _expand_path(value: str | Path | None, default: str) -> Path:
    raw = value or default
    return Path(raw).expanduser()


def _find_settings_file(explicit: Path | None) -> Path | None:
    """Locate ``config/settings.yaml`` regardless of the current cwd.

    Resolution order:

    1. ``explicit`` if provided and it exists.
    2. ``$AI_DM_SETTINGS`` if set and it exists.
    3. ``DEFAULT_SETTINGS_PATH`` resolved against cwd (legacy behaviour).
    4. ``DEFAULT_SETTINGS_PATH`` resolved against any ancestor of cwd
       that contains a repo marker (``pyproject.toml`` / ``.git``).
    5. ``DEFAULT_SETTINGS_PATH`` resolved against the package install
       root (``<repo>/src/ai_dm/.. = <repo>``) so editable installs
       launched from anywhere on the filesystem still find the file.
    """
    if explicit is not None and explicit.exists():
        return explicit
    env = os.environ.get("AI_DM_SETTINGS")
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p

    cwd_candidate = Path.cwd() / DEFAULT_SETTINGS_PATH
    if cwd_candidate.exists():
        return cwd_candidate

    # Walk up from cwd looking for a repo marker.
    here = Path.cwd().resolve()
    for parent in (here, *here.parents):
        if any((parent / marker).exists() for marker in _REPO_MARKERS):
            cand = parent / DEFAULT_SETTINGS_PATH
            if cand.exists():
                return cand
            break

    # Fall back to the package install root (works for editable installs
    # where the source tree contains the config/ dir alongside src/).
    pkg_root = Path(__file__).resolve().parents[3]  # …/src/ai_dm/app/settings.py → repo
    cand = pkg_root / DEFAULT_SETTINGS_PATH
    if cand.exists():
        return cand
    return None


@dataclass(frozen=True)
class CampaignsSettings:
    root: Path = Path("~/dnd/campaigns").expanduser()
    active: str | None = None
    state_root: Path = Path("data/campaigns")


@dataclass(frozen=True)
class Settings:
    raw: dict[str, Any]
    campaigns: CampaignsSettings

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        resolved = _find_settings_file(path)
        data: dict[str, Any] = {}
        if resolved is not None:
            try:
                import yaml  # type: ignore[import-not-found]

                data = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
                logger.info("settings loaded from %s", resolved)
            except Exception as exc:  # noqa: BLE001
                logger.warning("settings %s unreadable: %s", resolved, exc)
                data = {}
        else:
            # Loud, actionable warning — without it the runtime silently
            # falls back to the legacy assets/campaign layout (slug
            # 'default') and the user sees a confusing "no player
            # character loaded" banner with no obvious cause.
            looked_for = path or DEFAULT_SETTINGS_PATH
            logger.warning(
                "no settings.yaml found (looked for %s relative to cwd=%s, "
                "$AI_DM_SETTINGS, ancestor repo roots, and the package install "
                "root). Falling back to legacy assets/campaign layout — the "
                "active campaign in your config will be IGNORED. "
                "Run from the repo root or set AI_DM_SETTINGS=/path/to/settings.yaml.",
                looked_for, Path.cwd(),
            )
        c = (data.get("campaigns") or {})
        return cls(
            raw=data,
            campaigns=CampaignsSettings(
                root=_expand_path(c.get("root"), "~/dnd/campaigns"),
                active=(c.get("active") or None),
                state_root=_expand_path(c.get("state_root"), "data/campaigns"),
            ),
        )

