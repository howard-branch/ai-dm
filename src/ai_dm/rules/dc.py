"""Difficulty classes and spellcasting derived numbers.

Loads the named DC table and the spell-DC formulas from
``assets/srd5_2/core/dcs.json`` so Python and Foundry stay in sync.
"""
from __future__ import annotations

from ai_dm.rules.srd_core import load

_DATA = load("dcs")
NAMED_DC: dict[str, int] = dict(_DATA["named"])


def dc(name: str) -> int:
    """Resolve a named DC ("easy", "hard", ...) to its numeric value."""
    key = name.strip().lower()
    if key not in NAMED_DC:
        raise KeyError(f"unknown named DC {name!r}; valid: {sorted(NAMED_DC)}")
    return NAMED_DC[key]


def spell_save_dc(proficiency_bonus: int, ability_mod: int) -> int:
    """SRD: ``8 + proficiency bonus + spellcasting ability modifier``."""
    return 8 + int(proficiency_bonus) + int(ability_mod)


def spell_attack_bonus(proficiency_bonus: int, ability_mod: int) -> int:
    """SRD: ``proficiency bonus + spellcasting ability modifier``."""
    return int(proficiency_bonus) + int(ability_mod)


__all__ = ["NAMED_DC", "dc", "spell_attack_bonus", "spell_save_dc"]

