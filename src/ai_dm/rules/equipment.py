"""Top-level item lookup across the SRD equipment catalogs.

Provides :func:`get_item` that searches weapons → armor → adventuring
gear → tools → mounts/vehicles in order and returns a normalised
``Item`` tagged with its category, so callers (Inventory model,
character wizard, command handlers) don't need to know which catalog
owns a given slug.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ai_dm.rules import adventuring_gear, armor, mounts, tools, weapons

ItemKind = Literal["weapon", "armor", "gear", "tool", "mount_or_vehicle"]


@dataclass(frozen=True)
class Item:
    key: str
    name: str
    kind: ItemKind
    weight: float
    cost_amount: float
    cost_unit: str
    record: Any  # the underlying typed record from the per-category module


def _wrap(rec: Any, kind: ItemKind) -> Item:
    return Item(
        key=rec.key,
        name=rec.name,
        kind=kind,
        weight=float(getattr(rec, "weight", 0) or 0),
        cost_amount=float(getattr(rec, "cost_amount", 0) or 0),
        cost_unit=str(getattr(rec, "cost_unit", "gp")),
        record=rec,
    )


def get_item(key: str) -> Item | None:
    if (w := weapons.get_weapon(key)) is not None:
        return _wrap(w, "weapon")
    if (a := armor.get_armor(key)) is not None:
        return _wrap(a, "armor")
    if (g := adventuring_gear.get_gear(key)) is not None:
        return _wrap(g, "gear")
    if (t := tools.get_tool(key)) is not None:
        return _wrap(t, "tool")
    if (m := mounts.get_entry(key)) is not None:
        return _wrap(m, "mount_or_vehicle")
    return None


def kind_of(key: str) -> ItemKind | None:
    it = get_item(key)
    return it.kind if it else None


def weight_of(key: str, qty: int = 1) -> float:
    it = get_item(key)
    if it is None:
        return 0.0
    return float(it.weight) * max(int(qty), 0)


__all__ = ["Item", "ItemKind", "get_item", "kind_of", "weight_of"]

