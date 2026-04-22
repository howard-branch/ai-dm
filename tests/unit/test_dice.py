from ai_dm.rules.dice import roll_d20


def test_roll_d20_range():
    result = roll_d20()
    assert 1 <= result <= 20
