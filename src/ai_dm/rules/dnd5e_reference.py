"""Lightweight, MVP D&D 5e reference tables.

The campaign asset ``assets/rules/dnd5e_reference.md`` is intended as a
prose primer for the LLM. The mechanical tables below are the minimum the
:class:`RulesEngine` needs at runtime.

The ability and proficiency-bonus helpers now delegate to the
data-driven modules :mod:`ai_dm.rules.abilities` and
:mod:`ai_dm.rules.spell_progression` so the SRD JSON catalog is the
single source of truth.
"""
from __future__ import annotations

from ai_dm.rules.abilities import ABILITIES, Ability, modifier as _mod

# Standard 5e skill -> governing ability map.
SKILL_TO_ABILITY: dict[str, Ability] = {
    "acrobatics": "dex",
    "animal_handling": "wis",
    "arcana": "int",
    "athletics": "str",
    "deception": "cha",
    "history": "int",
    "insight": "wis",
    "intimidation": "cha",
    "investigation": "int",
    "medicine": "wis",
    "nature": "int",
    "perception": "wis",
    "performance": "cha",
    "persuasion": "cha",
    "religion": "int",
    "sleight_of_hand": "dex",
    "stealth": "dex",
    "survival": "wis",
}


def ability_modifier(score: int) -> int:
    """5e modifier from a 1-30 ability score."""
    return _mod(score)


def proficiency_for_level(level: int) -> int:
    """Standard SRD proficiency bonus by level (clamped to 1..20)."""
    from ai_dm.rules.srd_core import load
    table = load("proficiency")["by_level"]
    lvl = max(1, min(20, int(level)))
    return int(table[lvl - 1])


__all__ = [
    "ABILITIES",
    "Ability",
    "SKILL_TO_ABILITY",
    "ability_modifier",
    "proficiency_for_level",
]

