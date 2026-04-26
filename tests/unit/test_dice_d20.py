"""Tests for the unified d20-test API and adv/dis stacking."""
from __future__ import annotations

import pytest

from ai_dm.rules.dice import DiceRoller, combine_advantage, d20_test


@pytest.mark.parametrize(
    "adv,dis,expected",
    [
        (0, 0, "normal"),
        (1, 0, "advantage"),
        (5, 0, "advantage"),
        (0, 1, "disadvantage"),
        (0, 9, "disadvantage"),
        (1, 1, "normal"),
        (3, 2, "normal"),
        (-2, 1, "disadvantage"),  # clamped negative
    ],
)
def test_combine_advantage(adv: int, dis: int, expected: str) -> None:
    assert combine_advantage(adv, dis) == expected


def test_d20_check_pass_and_fail() -> None:
    # Seed chosen so the first d20 is high; we only need determinism.
    roller = DiceRoller(seed=1)
    r = d20_test(roller, modifier=3, dc=10)
    assert r.dc == 10
    assert r.total == r.roll + 3
    assert r.success is (r.total >= 10)
    assert r.target == 10


def test_d20_attack_nat20_auto_hits() -> None:
    # Pre-seed roller to roll a 20 by injecting via a stub.
    class Fake:
        rng = None
        def roll(self, expr, *, advantage="normal"):
            from ai_dm.rules.dice import RollResult
            return RollResult(expr, [20], [20], 0, 20, advantage, True, False)
    r = d20_test(Fake(), modifier=-100, ac=99, is_attack=True)
    assert r.crit is True
    assert r.success is True


def test_d20_attack_nat1_auto_misses() -> None:
    class Fake:
        rng = None
        def roll(self, expr, *, advantage="normal"):
            from ai_dm.rules.dice import RollResult
            return RollResult(expr, [1], [1], 0, 1, advantage, False, True)
    r = d20_test(Fake(), modifier=100, ac=5, is_attack=True)
    assert r.fumble is True
    assert r.success is False


def test_d20_check_no_auto_for_checks() -> None:
    """Nat 20 on a plain ability check is not an automatic success per SRD."""
    class Fake:
        rng = None
        def roll(self, expr, *, advantage="normal"):
            from ai_dm.rules.dice import RollResult
            return RollResult(expr, [20], [20], 0, 20, advantage, True, False)
    r = d20_test(Fake(), modifier=0, dc=25)
    assert r.crit is True
    assert r.success is False  # 20 + 0 < 25

