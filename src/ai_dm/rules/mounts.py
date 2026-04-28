"""SRD 5.2.1 mounts, draft animals, tack and vehicles.

Single source of truth: ``assets/srd5_2/core/mounts_vehicles.json``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai_dm.rules.srd_core import load

_DATA = load("mounts_vehicles")

EntryKind = Literal["mount", "draft", "vehicle_land", "vehicle_water", "tack"]


@dataclass(frozen=True)
class MountOrVehicle:
    key: str
    name: str
    kind: EntryKind
    cost_amount: float
    cost_unit: str
    weight: float = 0
    speed_ft: int | None = None
    speed_mph: float | None = None
    carry_capacity_lb: float | None = None
    cargo_tons: float | None = None
    crew: int | None = None
    passengers: int | None = None
    hp: int | None = None
    ac: int | None = None
    damage_threshold: int | None = None


_BY_KEY: dict[str, MountOrVehicle] = {
    rec["key"]: MountOrVehicle(
        key=rec["key"],
        name=rec["name"],
        kind=rec["kind"],
        cost_amount=float((rec.get("cost") or {}).get("amount", 0)),
        cost_unit=str((rec.get("cost") or {}).get("unit", "gp")),
        weight=float(rec.get("weight", 0)),
        speed_ft=rec.get("speed_ft"),
        speed_mph=rec.get("speed_mph"),
        carry_capacity_lb=rec.get("carry_capacity_lb"),
        cargo_tons=rec.get("cargo_tons"),
        crew=rec.get("crew"),
        passengers=rec.get("passengers"),
        hp=rec.get("hp"),
        ac=rec.get("ac"),
        damage_threshold=rec.get("damage_threshold"),
    )
    for rec in _DATA["entries"]
}


def get_entry(key: str) -> MountOrVehicle | None:
    return _BY_KEY.get(key)


def all_entries() -> list[MountOrVehicle]:
    return list(_BY_KEY.values())


__all__ = ["EntryKind", "MountOrVehicle", "all_entries", "get_entry"]

