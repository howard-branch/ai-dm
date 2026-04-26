"""Tests for SRD 5.2 (2024) one-track exhaustion."""
from __future__ import annotations

import pytest

from ai_dm.rules.exhaustion import (
    DEATH_AT,
    MAX_LEVEL,
    add,
    clamp,
    d20_penalty,
    is_dead,
    remove,
    speed_penalty,
)


def test_clamp_bounds() -> None:
    assert clamp(-3) == 0
    assert clamp(0) == 0
    assert clamp(MAX_LEVEL) == MAX_LEVEL
    assert clamp(MAX_LEVEL + 7) == MAX_LEVEL


@pytest.mark.parametrize(
    "level,d20,speed",
    [(0, 0, 0), (1, -2, -5), (2, -4, -10),
     (3, -6, -15), (5, -10, -25), (6, -12, -30)],
)
def test_penalty_curves(level: int, d20: int, speed: int) -> None:
    assert d20_penalty(level) == d20
    assert speed_penalty(level) == speed


def test_add_remove_clamp() -> None:
    assert add(0, 1) == 1
    assert add(5, 5) == MAX_LEVEL
    assert remove(2, 5) == 0
    assert remove(MAX_LEVEL, 1) == MAX_LEVEL - 1


def test_death_at_max() -> None:
    assert DEATH_AT == MAX_LEVEL == 6
    assert is_dead(5) is False
    assert is_dead(6) is True
    assert is_dead(99) is True

