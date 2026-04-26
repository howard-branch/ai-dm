"""Ability scores, modifiers and saving-throw helpers.

Single source of truth: ``assets/srd5_2/core/abilities.json``.
"""
from __future__ import annotations

from typing import Iterable, Literal, Mapping

from ai_dm.rules.srd_core import load

_DATA = load("abilities")

Ability = Literal["str", "dex", "con", "int", "wis", "cha"]
ABILITIES: tuple[Ability, ...] = tuple(_DATA["abilities"])  # type: ignore[assignment]
ABILITY_LABELS: dict[str, str] = dict(_DATA["labels"])
SCORE_MIN: int = int(_DATA["score_min"])
SCORE_MAX: int = int(_DATA["score_max"])
DEFAULT_SCORE: int = int(_DATA["default_score"])


def validate_score(score: int) -> int:
    """Return ``score`` if within SRD bounds, else raise ``ValueError``."""
    s = int(score)
    if s < SCORE_MIN or s > SCORE_MAX:
        raise ValueError(
            f"ability score {s} outside SRD range {SCORE_MIN}..{SCORE_MAX}"
        )
    return s


def modifier(score: int) -> int:
    """5e ability modifier: ``floor((score - 10) / 2)`` for 1..30."""
    return (validate_score(score) - 10) // 2


def ability_mods(scores: Mapping[str, int]) -> dict[str, int]:
    """Map ``{ability: score}`` → ``{ability: modifier}`` for the six
    canonical abilities. Missing scores default to ``DEFAULT_SCORE``
    (modifier 0); unknown keys are ignored.
    """
    out: dict[str, int] = {}
    for ab in ABILITIES:
        out[ab] = modifier(int(scores.get(ab, DEFAULT_SCORE)))
    return out


def saving_throw_mod(
    scores: Mapping[str, int],
    *,
    proficiency_bonus: int,
    proficient_in: Iterable[str] = (),
) -> dict[str, int]:
    """Per-ability saving-throw bonus = ability_mod (+ PB if proficient)."""
    prof = set(p.lower() for p in proficient_in)
    mods = ability_mods(scores)
    return {
        ab: mods[ab] + (int(proficiency_bonus) if ab in prof else 0)
        for ab in ABILITIES
    }


__all__ = [
    "ABILITIES",
    "ABILITY_LABELS",
    "Ability",
    "DEFAULT_SCORE",
    "SCORE_MAX",
    "SCORE_MIN",
    "ability_mods",
    "modifier",
    "saving_throw_mod",
    "validate_score",
]

