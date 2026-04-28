"""SRD 5.2.1 armor + shield catalog and AC math.

Single source of truth: ``assets/srd5_2/core/armor.json``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from ai_dm.rules.srd_core import load

_DATA = load("armor")

ArmorCategory = Literal["light", "medium", "heavy", "shield"]
DexMode = Literal["add", "add_max_2", "none", "flat"]
UNARMORED_AC: int = 10  # SRD: AC 10 + Dex when wearing no armor


@dataclass(frozen=True)
class Armor:
    key: str
    name: str
    category: ArmorCategory
    cost_amount: float
    cost_unit: str
    weight: float
    base_ac: int
    dex_mode: DexMode
    strength_req: int | None
    stealth_disadvantage: bool
    don_time: str
    doff_time: str


def _from_record(rec: dict) -> Armor:
    ac = rec.get("ac") or {}
    return Armor(
        key=str(rec["key"]),
        name=str(rec["name"]),
        category=rec["category"],
        cost_amount=float((rec.get("cost") or {}).get("amount", 0)),
        cost_unit=str((rec.get("cost") or {}).get("unit", "gp")),
        weight=float(rec.get("weight", 0)),
        base_ac=int(ac.get("base", 0)),
        dex_mode=str(ac.get("dex", "none")),  # type: ignore[arg-type]
        strength_req=rec.get("strength_req"),
        stealth_disadvantage=bool(rec.get("stealth_disadvantage", False)),
        don_time=str(rec.get("don_time", "")),
        doff_time=str(rec.get("doff_time", "")),
    )


_BY_KEY: dict[str, Armor] = {rec["key"]: _from_record(rec) for rec in _DATA["armors"]}


def get_armor(key: str) -> Armor | None:
    return _BY_KEY.get(key)


def all_armor() -> list[Armor]:
    return list(_BY_KEY.values())


def iter_armor(*, category: str | None = None) -> Iterable[Armor]:
    for a in _BY_KEY.values():
        if category is None or a.category == category:
            yield a


def _dex_for(mode: DexMode, dex_mod: int) -> int:
    if mode == "add":
        return int(dex_mod)
    if mode == "add_max_2":
        return min(int(dex_mod), 2)
    # 'none' and 'flat' contribute no Dex (shields are flat AC bonuses)
    return 0


def compute_ac(
    armor: Armor | None,
    dex_mod: int,
    *,
    shield: Armor | None = None,
) -> int:
    """Compute total AC with optional armor and shield.

    * No armor → ``UNARMORED_AC + dex_mod`` (the SRD baseline).
    * Light / Medium / Heavy → uses the armor's Dex mode.
    * Shield (``category='shield'``, ``dex='flat'``) → adds ``base_ac``.
    """
    if armor is None:
        ac = UNARMORED_AC + int(dex_mod)
    elif armor.dex_mode == "flat":
        # A shield passed as the primary armor argument is a misuse,
        # but degrade gracefully.
        ac = UNARMORED_AC + int(dex_mod) + armor.base_ac
    else:
        ac = armor.base_ac + _dex_for(armor.dex_mode, dex_mod)
    if shield is not None and shield.dex_mode == "flat":
        ac += shield.base_ac
    return int(ac)


def meets_strength_requirement(armor: Armor, strength_score: int) -> bool:
    """``True`` when the wearer hits the armor's STR threshold (or there is none)."""
    if armor.strength_req is None:
        return True
    return int(strength_score) >= int(armor.strength_req)


def imposes_stealth_disadvantage(armor: Armor | None) -> bool:
    return bool(armor and armor.stealth_disadvantage)


__all__ = [
    "Armor", "ArmorCategory", "DexMode", "UNARMORED_AC",
    "all_armor", "compute_ac", "get_armor",
    "imposes_stealth_disadvantage", "iter_armor",
    "meets_strength_requirement",
]

