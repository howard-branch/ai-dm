import pytest

from ai_dm.rules.dice import DiceRoller, roll, roll_d20


def test_roll_d20_range():
    # legacy contract preserved
    for _ in range(20):
        assert 1 <= roll_d20() <= 20


def test_simple_expression_seeded():
    r = DiceRoller(seed=42)
    res = r.roll("2d6+3")
    assert res.expression == "2d6+3"
    assert len(res.rolls) == 2
    assert res.modifier == 3
    assert res.total == sum(res.rolls) + 3


def test_advantage_keeps_higher():
    r = DiceRoller(seed=1)
    res = r.roll("1d20", advantage="advantage")
    assert len(res.rolls) == 2
    assert len(res.kept) == 1
    assert res.kept[0] == max(res.rolls)


def test_disadvantage_keeps_lower():
    r = DiceRoller(seed=1)
    res = r.roll("1d20", advantage="disadvantage")
    assert res.kept[0] == min(res.rolls)


def test_keep_highest_explicit():
    r = DiceRoller(seed=2)
    res = r.roll("4d6kh3")
    assert len(res.rolls) == 4
    assert len(res.kept) == 3
    assert res.kept == sorted(res.rolls, reverse=True)[:3]


def test_negative_modifier():
    r = DiceRoller(seed=0)
    res = r.roll("1d8-1")
    assert res.modifier == -1
    assert res.total == res.rolls[0] - 1


def test_invalid_expression():
    r = DiceRoller()
    with pytest.raises(ValueError):
        r.roll("garbage")


def test_module_roll_helper():
    res = roll("1d4")
    assert 1 <= res.total <= 4


def test_crit_and_fumble_flags():
    # Force a deterministic 20 by seeding until we hit one — quick scan.
    r = DiceRoller(seed=0)
    seen_crit = False
    seen_fumble = False
    for _ in range(200):
        res = r.roll("1d20")
        seen_crit = seen_crit or res.crit
        seen_fumble = seen_fumble or res.fumble
        if seen_crit and seen_fumble:
            break
    assert seen_crit and seen_fumble

