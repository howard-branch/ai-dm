"""Tests for ``ai_dm.rules.abilities``."""
from __future__ import annotations

import pytest

from ai_dm.rules.abilities import (
    ABILITIES,
    SCORE_MAX,
    SCORE_MIN,
    ability_mods,
    modifier,
    saving_throw_mod,
    validate_score,
)


def test_abilities_canonical_six() -> None:
    assert ABILITIES == ("str", "dex", "con", "int", "wis", "cha")


@pytest.mark.parametrize(
    "score,mod",
    [(1, -5), (3, -4), (8, -1), (10, 0), (11, 0), (12, 1),
     (15, 2), (20, 5), (24, 7), (30, 10)],
)
def test_modifier_curve(score: int, mod: int) -> None:
    assert modifier(score) == mod


def test_score_bounds() -> None:
    with pytest.raises(ValueError):
        validate_score(SCORE_MIN - 1)
    with pytest.raises(ValueError):
        validate_score(SCORE_MAX + 1)
    assert validate_score(SCORE_MIN) == SCORE_MIN
    assert validate_score(SCORE_MAX) == SCORE_MAX


def test_ability_mods_defaults_to_ten() -> None:
    mods = ability_mods({"str": 16, "dex": 14})
    assert mods == {"str": 3, "dex": 2, "con": 0, "int": 0, "wis": 0, "cha": 0}


def test_saving_throw_mod_adds_pb_to_proficient_only() -> None:
    scores = {"str": 16, "dex": 12, "con": 14, "int": 8, "wis": 10, "cha": 10}
    saves = saving_throw_mod(scores, proficiency_bonus=3, proficient_in=("str", "con"))
    assert saves["str"] == 3 + 3       # mod +3, prof +3
    assert saves["con"] == 2 + 3
    assert saves["dex"] == 1
    assert saves["int"] == -1

