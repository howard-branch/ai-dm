"""SRD 5.2.1 weapons catalog + per-weapon helpers.

Single source of truth: ``assets/srd5_2/core/weapons.json``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

from ai_dm.rules.srd_core import load

_DATA = load("weapons")

WeaponCategory = Literal["simple_melee", "simple_ranged", "martial_melee", "martial_ranged"]


@dataclass(frozen=True)
class Range:
    normal: int
    long: int


@dataclass(frozen=True)
class Damage:
    dice: str
    type: str
    versatile: str | None = None


@dataclass(frozen=True)
class Weapon:
    key: str
    name: str
    category: WeaponCategory
    cost_amount: float
    cost_unit: str
    weight: float
    damage: Damage
    properties: tuple[str, ...]
    range: Range | None
    mastery: str | None
    ammunition_type: str | None = None


def _from_record(rec: dict) -> Weapon:
    dmg = rec.get("damage") or {}
    rng = rec.get("range")
    return Weapon(
        key=str(rec["key"]),
        name=str(rec["name"]),
        category=rec["category"],
        cost_amount=float((rec.get("cost") or {}).get("amount", 0)),
        cost_unit=str((rec.get("cost") or {}).get("unit", "gp")),
        weight=float(rec.get("weight", 0)),
        damage=Damage(dice=str(dmg.get("dice", "")), type=str(dmg.get("type", "")),
                      versatile=dmg.get("versatile")),
        properties=tuple(rec.get("properties") or ()),
        range=Range(normal=int(rng["normal"]), long=int(rng["long"])) if rng else None,
        mastery=rec.get("mastery"),
        ammunition_type=rec.get("ammunition_type"),
    )


_BY_KEY: dict[str, Weapon] = {rec["key"]: _from_record(rec) for rec in _DATA["weapons"]}


def get_weapon(key: str) -> Weapon | None:
    return _BY_KEY.get(key)


def all_weapons() -> list[Weapon]:
    return list(_BY_KEY.values())


def iter_weapons(*, category: str | None = None) -> Iterable[Weapon]:
    for w in _BY_KEY.values():
        if category is None or w.category == category:
            yield w


def has_property(weapon: Weapon, prop: str) -> bool:
    return prop in weapon.properties


def is_finesse(w: Weapon) -> bool:    return "finesse" in w.properties
def is_light(w: Weapon) -> bool:      return "light" in w.properties
def is_heavy(w: Weapon) -> bool:      return "heavy" in w.properties
def is_thrown(w: Weapon) -> bool:     return "thrown" in w.properties
def is_two_handed(w: Weapon) -> bool: return "two_handed" in w.properties
def has_reach(w: Weapon) -> bool:     return "reach" in w.properties
def is_ranged(w: Weapon) -> bool:     return w.category.endswith("ranged")
def is_versatile(w: Weapon) -> bool:  return "versatile" in w.properties


def damage_for(weapon: Weapon, *, two_handed: bool = False) -> tuple[str, str]:
    """Return ``(dice, type)``; uses the versatile die when ``two_handed``."""
    if two_handed and weapon.damage.versatile:
        return (weapon.damage.versatile, weapon.damage.type)
    return (weapon.damage.dice, weapon.damage.type)


def attack_range(weapon: Weapon) -> tuple[int, int]:
    """Return ``(normal_ft, long_ft)``. Pure-melee weapons return ``(reach, reach)``."""
    if weapon.range is not None:
        return (weapon.range.normal, weapon.range.long)
    reach = 10 if has_reach(weapon) else 5
    return (reach, reach)


__all__ = [
    "Damage", "Range", "Weapon", "WeaponCategory",
    "all_weapons", "attack_range", "damage_for",
    "get_weapon", "has_property", "has_reach",
    "is_finesse", "is_heavy", "is_light", "is_ranged",
    "is_thrown", "is_two_handed", "is_versatile",
    "iter_weapons",
]

