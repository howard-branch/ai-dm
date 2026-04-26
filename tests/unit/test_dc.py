"""Tests for difficulty-class helpers."""
from __future__ import annotations

import pytest

from ai_dm.rules.dc import NAMED_DC, dc, spell_attack_bonus, spell_save_dc


def test_named_dc_table() -> None:
    assert NAMED_DC["very_easy"] == 5
    assert NAMED_DC["easy"] == 10
    assert NAMED_DC["medium"] == 15
    assert NAMED_DC["hard"] == 20
    assert NAMED_DC["very_hard"] == 25
    assert NAMED_DC["nearly_impossible"] == 30


def test_dc_lookup_case_insensitive() -> None:
    assert dc("EASY") == 10
    assert dc("  hard ") == 20


def test_unknown_dc_raises() -> None:
    with pytest.raises(KeyError):
        dc("impossible")


def test_spell_save_dc_formula() -> None:
    # PB +3, ability mod +4 → 8 + 3 + 4 = 15
    assert spell_save_dc(3, 4) == 15
    # Negative mod still computes
    assert spell_save_dc(2, -1) == 9


def test_spell_attack_bonus_formula() -> None:
    assert spell_attack_bonus(3, 4) == 7
    assert spell_attack_bonus(2, 0) == 2

