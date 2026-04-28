"""Cover — SRD 5.2.

Single source of truth: ``assets/srd5_2/core/cover.json``.

Cover grants a bonus to AC and Dexterity saving throws against effects
that originate outside the cover. Total cover blocks the line of
effect entirely.
"""
from __future__ import annotations

from typing import Literal

from ai_dm.rules.srd_core import load

_DATA = load("cover")
CoverKey = Literal["none", "half", "three_quarters", "total"]

_LEVELS = {rec["key"]: rec for rec in _DATA["levels"]}
COVER_KEYS: tuple[str, ...] = tuple(_LEVELS.keys())


def _level(key: str) -> dict:
    return _LEVELS.get(key, _LEVELS["none"])


def ac_bonus(cover: str) -> int:
    val = _level(cover).get("ac")
    return 0 if val is None else int(val)


def dex_save_bonus(cover: str) -> int:
    val = _level(cover).get("save")
    return 0 if val is None else int(val)


def blocks(cover: str) -> bool:
    """True if the cover prevents the attack/effect entirely (total cover)."""
    return bool(_level(cover).get("blocks", False))


def apply_to_target_ac(target_ac: int, *, cover: str) -> int:
    """Effective AC the attacker must beat after cover."""
    return int(target_ac) + ac_bonus(cover)


__all__ = [
    "COVER_KEYS",
    "CoverKey",
    "ac_bonus",
    "apply_to_target_ac",
    "blocks",
    "dex_save_bonus",
]

