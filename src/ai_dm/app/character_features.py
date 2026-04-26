"""Class + background feature loader for the character wizard."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ai_dm.app.catalog_loader import load_overlay

if TYPE_CHECKING:
    from ai_dm.campaign.pack import CampaignPack

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FEATURES_PATH = _REPO_ROOT / "assets" / "rules" / "dnd5e_class_features.json"


def load_class_features(
    path: Path | None = None,
    *,
    pack: "CampaignPack | None" = None,
) -> dict[str, Any]:
    if path is not None:
        if not path.exists():
            return {}
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    return load_overlay("dnd5e_class_features.json", pack=pack)


def features_for(
    archetype_key: str,
    background_key: str | None,
    *,
    catalog: dict[str, Any] | None = None,
    pack: "CampaignPack | None" = None,
) -> list[dict[str, Any]]:
    """Return the structured feature list for an archetype + background."""
    catalog = catalog if catalog is not None else load_class_features(pack=pack)
    out: list[dict[str, Any]] = []
    class_feats = catalog.get(archetype_key) or []
    if isinstance(class_feats, list):
        for f in class_feats:
            if isinstance(f, dict):
                # Tag the source so the player can see where it came from.
                feat = dict(f)
                feat.setdefault("source", f"class:{archetype_key}:1")
                out.append(feat)
    if background_key:
        bg_map = catalog.get("_backgrounds") or {}
        bg_feats = bg_map.get(background_key) or []
        if isinstance(bg_feats, list):
            for f in bg_feats:
                if isinstance(f, dict):
                    feat = dict(f)
                    feat.setdefault("source", f"background:{background_key}")
                    out.append(feat)
    return out

