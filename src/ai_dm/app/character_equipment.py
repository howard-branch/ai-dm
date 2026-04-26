"""Starting equipment + shopping helpers for the character wizard.

Pure functions plus tiny disk-cached loaders so tests can override the
catalog by passing an explicit mapping. All catalog files live under
``assets/rules/`` at the repo root.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ai_dm.app.catalog_loader import load_overlay

if TYPE_CHECKING:
    from ai_dm.campaign.pack import CampaignPack

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ITEMS_PATH = _REPO_ROOT / "assets" / "rules" / "dnd5e_items.json"
_KITS_PATH = _REPO_ROOT / "assets" / "rules" / "dnd5e_starting_kits.json"


def _strip_doc(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def load_items_catalog(
    path: Path | None = None,
    *,
    pack: "CampaignPack | None" = None,
) -> dict[str, dict[str, Any]]:
    """Return ``{item_id: item_record}`` (excluding ``_doc``).

    When ``pack`` is given, ``<pack>/rules/dnd5e_items.json`` is
    deep-merged on top of the shared catalog. ``path`` overrides the
    shared base for tests.
    """
    if path is not None:
        if not path.exists():
            return {}
        with path.open(encoding="utf-8") as fh:
            return _strip_doc(json.load(fh))
    return load_overlay("dnd5e_items.json", pack=pack)


def load_starting_kits(
    path: Path | None = None,
    *,
    pack: "CampaignPack | None" = None,
) -> dict[str, dict[str, Any]]:
    """Return ``{archetype_key: kit_record}`` (excluding ``_doc``)."""
    if path is not None:
        if not path.exists():
            return {}
        with path.open(encoding="utf-8") as fh:
            return _strip_doc(json.load(fh))
    return load_overlay("dnd5e_starting_kits.json", pack=pack)


def _materialise_item(item_id: str, qty: int, equipped: bool, catalog: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    rec = catalog.get(item_id)
    if rec is None:
        return None
    out: dict[str, Any] = {
        "id": item_id,
        "name": rec.get("name", item_id),
        "type": rec.get("type", "gear"),
        "qty": int(qty),
    }
    if equipped:
        out["equipped"] = True
    for field in (
        "weight", "value_gp", "description",
        "damage", "weapon_type", "properties", "range",
        "armor", "ac_bonus",
    ):
        if field in rec:
            out[field] = rec[field]
    return out


def apply_kit(
    archetype_key: str,
    *,
    catalog: dict[str, dict[str, Any]] | None = None,
    kits: dict[str, dict[str, Any]] | None = None,
    pack: "CampaignPack | None" = None,
) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    """Allot the default kit for ``archetype_key``.

    Returns ``(inventory, currency, shopping_budget_gp)``. Unknown
    archetype → empty kit, zero gold, zero budget. ``pack`` opts into
    per-pack catalog overlays for both items and kits when neither
    ``catalog`` nor ``kits`` is supplied explicitly.
    """
    catalog = catalog if catalog is not None else load_items_catalog(pack=pack)
    kits = kits if kits is not None else load_starting_kits(pack=pack)
    kit = kits.get(archetype_key) or {}
    inventory: list[dict[str, Any]] = []
    for entry in kit.get("items", []) or []:
        item = _materialise_item(
            str(entry.get("id", "")),
            int(entry.get("qty", 1)),
            bool(entry.get("equipped", False)),
            catalog,
        )
        if item is not None:
            inventory.append(item)
    raw_currency = dict(kit.get("currency") or {})
    currency = {k: int(raw_currency.get(k, 0)) for k in ("pp", "gp", "ep", "sp", "cp")}
    budget = int(kit.get("shopping_budget_gp", 0))
    return inventory, currency, budget


def apply_purchases(
    inventory: list[dict[str, Any]],
    currency: dict[str, int],
    budget_gp: int,
    picks: list[str | dict[str, Any]] | None,
    *,
    catalog: dict[str, dict[str, Any]] | None = None,
    pack: "CampaignPack | None" = None,
) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    """Spend up to ``budget_gp`` on items from ``catalog``.

    ``picks`` may be a list of item ids or ``{"id": ..., "qty": N}``
    dicts. Items whose total cost exceeds remaining budget are skipped
    and reported in the returned ``errors`` list. Any leftover budget
    is added to ``currency['gp']``.

    The input lists are not mutated; new copies are returned.
    """
    catalog = catalog if catalog is not None else load_items_catalog(pack=pack)
    inv = [dict(it) for it in inventory]
    cur = dict(currency)
    cur.setdefault("gp", 0)
    errors: list[str] = []
    remaining = int(budget_gp)
    for raw in picks or []:
        if isinstance(raw, str):
            item_id, qty = raw, 1
        elif isinstance(raw, dict):
            item_id = str(raw.get("id", "")).strip()
            qty = int(raw.get("qty", 1) or 1)
        else:
            errors.append(f"ignored unsupported pick entry {raw!r}")
            continue
        rec = catalog.get(item_id)
        if rec is None:
            errors.append(f"unknown item {item_id!r}")
            continue
        unit_price = int(rec.get("value_gp", 0) or 0)
        cost = unit_price * max(qty, 1)
        if cost > remaining:
            errors.append(
                f"{rec.get('name', item_id)}: cost {cost} gp exceeds remaining budget {remaining} gp"
            )
            continue
        remaining -= cost
        item = _materialise_item(item_id, qty, False, catalog)
        if item is not None:
            # Merge with an existing entry of the same id, otherwise append.
            for existing in inv:
                if existing.get("id") == item_id and not existing.get("equipped"):
                    existing["qty"] = int(existing.get("qty", 1)) + qty
                    break
            else:
                inv.append(item)
    cur["gp"] = int(cur.get("gp", 0)) + remaining
    return inv, cur, errors

