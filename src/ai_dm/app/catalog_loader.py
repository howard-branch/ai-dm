"""Shared loader for ``assets/rules/*.json`` catalogs with optional
per-campaign-pack overlays.

Layout
------

Shared (always loaded)::

    assets/rules/dnd5e_items.json
    assets/rules/dnd5e_spells.json
    assets/rules/dnd5e_starting_kits.json
    assets/rules/dnd5e_class_features.json

Per-pack overlay (optional, layered *on top* of the shared catalog)::

    <pack.root>/rules/dnd5e_items.json          # themed gear
    <pack.root>/rules/dnd5e_starting_kits.json  # themed kits
    <pack.root>/rules/dnd5e_spells.json         # themed spells
    <pack.root>/rules/dnd5e_class_features.json # themed features

The overlay path is :pyattr:`ai_dm.campaign.pack.CampaignPaths.rules`,
which defaults to ``<pack.root>/rules/`` and can be relocated via
``campaign.yaml``'s ``overrides.rules``.

Merge semantics
---------------

* Top-level keys: overlay wins.
* When both base value and overlay value are ``dict``, they are merged
  recursively under the same rules.
* Lists (and any non-dict values) are **replaced wholesale** by the
  overlay — there is no append / extend sentinel in v1. This keeps
  overlays predictable; if a pack wants to add to a list it must restate
  the full list.
* Keys starting with ``_`` (e.g. ``_doc``) are stripped from the final
  result.

The merge is non-destructive (input dicts are never mutated).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai_dm.campaign.pack import CampaignPack

logger = logging.getLogger("ai_dm.app.catalog_loader")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHARED_RULES_DIR = _REPO_ROOT / "assets" / "rules"


def _strip_doc(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if not (isinstance(k, str) and k.startswith("_"))}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("catalog %s unreadable: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge. Overlay wins; nested dicts merge; lists
    and scalars are replaced. Inputs are not mutated.
    """
    out: dict[str, Any] = dict(base)
    for k, v in overlay.items():
        if (
            k in out
            and isinstance(out[k], dict)
            and isinstance(v, dict)
        ):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_overlay(
    filename: str,
    *,
    pack: "CampaignPack | None" = None,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Load ``<base_dir>/<filename>`` then merge ``<pack.paths.rules>/<filename>``
    on top, returning the merged catalog with ``_doc`` keys stripped.

    ``base_dir`` defaults to the repo's ``assets/rules/`` directory.
    Either side may be missing — a missing base + missing overlay
    yields ``{}``.
    """
    base_dir = base_dir or _SHARED_RULES_DIR
    base = _read_json(base_dir / filename)
    if pack is not None:
        overlay_path = pack.paths.rules / filename
        if overlay_path.exists():
            overlay = _read_json(overlay_path)
            if overlay:
                logger.info(
                    "applying pack overlay %s for %s",
                    overlay_path, filename,
                )
                base = deep_merge(base, overlay)
    return _strip_doc(base)

