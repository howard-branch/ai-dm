"""Lightweight, MVP D&D 5e reference tables.

The campaign asset ``assets/rules/dnd5e_reference.md`` is intended as a
prose primer for the LLM. The mechanical tables below are the minimum the
:class:`RulesEngine` needs at runtime.
"""
from __future__ import annotations

from typing import Literal

Ability = Literal["str", "dex", "con", "int", "wis", "cha"]
ABILITIES: tuple[Ability, ...] = ("str", "dex", "con", "int", "wis", "cha")

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
    return (score - 10) // 2


def proficiency_for_level(level: int) -> int:
    if level <= 0:
        return 2
    if level >= 17:
        return 6
    return 2 + (level - 1) // 4

