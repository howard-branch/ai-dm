"""Adventuring gear, ammunition, focuses and equipment packs.

Single source of truth: ``assets/srd5_2/core/adventuring_gear.json``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai_dm.rules.srd_core import load

_DATA = load("adventuring_gear")

GearCategory = Literal["gear", "pack", "ammunition", "focus", "consumable", "container"]


@dataclass(frozen=True)
class Gear:
    key: str
    name: str
    category: GearCategory
    cost_amount: float
    cost_unit: str
    weight: float
    quantity: int = 1
    container_capacity_lb: float | None = None
    contents: tuple[tuple[str, int], ...] = ()
    focus_for: tuple[str, ...] = ()
    ammunition_type: str | None = None


def _from_record(rec: dict) -> Gear:
    return Gear(
        key=str(rec["key"]),
        name=str(rec["name"]),
        category=rec["category"],
        cost_amount=float((rec.get("cost") or {}).get("amount", 0)),
        cost_unit=str((rec.get("cost") or {}).get("unit", "gp")),
        weight=float(rec.get("weight", 0)),
        quantity=int(rec.get("quantity", 1)),
        container_capacity_lb=rec.get("container_capacity_lb"),
        contents=tuple((c["ref"], int(c.get("qty", 1))) for c in (rec.get("contents") or [])),
        focus_for=tuple(rec.get("focus_for") or ()),
        ammunition_type=rec.get("ammunition_type"),
    )


_BY_KEY: dict[str, Gear] = {rec["key"]: _from_record(rec) for rec in _DATA["items"]}


def get_gear(key: str) -> Gear | None:
    return _BY_KEY.get(key)


def all_gear() -> list[Gear]:
    return list(_BY_KEY.values())


def expand_pack(pack_key: str) -> list[tuple[str, int]]:
    """Return the ``[(item_key, qty), ...]`` contents of a pack.

    For non-pack items returns ``[(pack_key, 1)]`` so callers can blindly
    pipe a starting-equipment slug through and get back a flat shopping list.
    """
    rec = _BY_KEY.get(pack_key)
    if rec is None or rec.category != "pack":
        return [(pack_key, 1)]
    return list(rec.contents)


__all__ = ["Gear", "GearCategory", "all_gear", "expand_pack", "get_gear"]

