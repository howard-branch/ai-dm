"""Tests for damage / healing / temp-HP / resistance / vulnerability / immunity."""
from __future__ import annotations

import pytest

from ai_dm.rules.damage import (
    DAMAGE_TYPES,
    apply_damage,
    apply_healing,
    apply_modifiers,
    grant_temp_hp,
)


def test_thirteen_canonical_damage_types() -> None:
    expected = {
        "acid", "bludgeoning", "cold", "fire", "force", "lightning",
        "necrotic", "piercing", "poison", "psychic", "radiant",
        "slashing", "thunder",
    }
    assert set(DAMAGE_TYPES) == expected


def test_resistance_halves_round_down() -> None:
    assert apply_modifiers(7, "fire", resistances=["fire"]) == 3
    assert apply_modifiers(1, "fire", resistances=["fire"]) == 0


def test_vulnerability_doubles() -> None:
    assert apply_modifiers(5, "cold", vulnerabilities=["cold"]) == 10


def test_immunity_zeroes() -> None:
    assert apply_modifiers(99, "poison", immunities=["poison"]) == 0


def test_immunity_beats_vulnerability() -> None:
    assert apply_modifiers(
        10, "fire", vulnerabilities=["fire"], immunities=["fire"],
    ) == 0


def test_resistance_and_vulnerability_cancel() -> None:
    assert apply_modifiers(
        12, "fire", resistances=["fire"], vulnerabilities=["fire"],
    ) == 12


def test_other_type_unaffected() -> None:
    assert apply_modifiers(8, "slashing", resistances=["fire"]) == 8


# ----- apply_damage / temp HP / heal --------------------------------------


def test_apply_damage_soaks_temp_hp_first() -> None:
    target = {"hp": 20, "max_hp": 20, "temp_hp": 5}
    out = apply_damage(target, 3, "fire")
    assert target["temp_hp"] == 2
    assert target["hp"] == 20
    assert out.absorbed_by_temp_hp == 3
    assert out.dealt == 0
    assert out.dropped_to_zero is False


def test_apply_damage_overflow_through_temp_hp() -> None:
    target = {"hp": 10, "max_hp": 20, "temp_hp": 4}
    out = apply_damage(target, 9, "fire")
    assert target["temp_hp"] == 0
    assert target["hp"] == 5
    assert out.absorbed_by_temp_hp == 4
    assert out.dealt == 5


def test_apply_damage_drops_to_zero_flag() -> None:
    target = {"hp": 3, "max_hp": 20, "temp_hp": 0}
    out = apply_damage(target, 5, "slashing")
    assert target["hp"] == 0
    assert out.dropped_to_zero is True


def test_apply_damage_zero_amount_noop() -> None:
    target = {"hp": 12, "max_hp": 20, "temp_hp": 2}
    out = apply_damage(target, 0)
    assert out.dealt == 0
    assert target["hp"] == 12 and target["temp_hp"] == 2


def test_apply_healing_capped_at_max_hp() -> None:
    target = {"hp": 18, "max_hp": 20, "temp_hp": 0}
    assert apply_healing(target, 99) == 20


def test_apply_healing_from_zero_returns_to_at_least_one() -> None:
    target = {"hp": 0, "max_hp": 20, "temp_hp": 0}
    assert apply_healing(target, 1) == 1


def test_apply_healing_zero_or_negative_noop() -> None:
    target = {"hp": 5, "max_hp": 10, "temp_hp": 0}
    assert apply_healing(target, 0) == 5
    assert apply_healing(target, -3) == 5


def test_temp_hp_takes_higher_does_not_stack() -> None:
    target = {"hp": 10, "max_hp": 10, "temp_hp": 0}
    assert grant_temp_hp(target, 5) == 5
    assert grant_temp_hp(target, 3) == 5     # lower → keep 5
    assert grant_temp_hp(target, 8) == 8     # higher → replace

