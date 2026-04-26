"""Tests for the death-save state machine."""
from __future__ import annotations

from ai_dm.rules.death_saves import (
    DeathSaveTrack,
    damage_at_zero,
    is_massive_damage,
    roll_death_save,
)
from ai_dm.rules.dice import RollResult


class _FixedRoller:
    """Roller stub that returns d20 results from a queue."""
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def roll(self, expr: str, *, advantage: str = "normal") -> RollResult:
        v = self._values.pop(0)
        crit = v == 20
        fumble = v == 1
        return RollResult(expr, [v], [v], 0, v, advantage, crit, fumble)


def test_three_successes_stable() -> None:
    track = DeathSaveTrack()
    roller = _FixedRoller([10, 11, 12])
    for _ in range(3):
        roll_death_save(track, roller)
    assert track.stable is True
    assert track.successes == 3
    assert track.failures == 0
    assert track.dead is False


def test_three_failures_dead() -> None:
    track = DeathSaveTrack()
    roller = _FixedRoller([5, 9, 4])
    for _ in range(3):
        roll_death_save(track, roller)
    assert track.dead is True
    assert track.failures == 3


def test_nat20_revives_to_one_hp() -> None:
    track = DeathSaveTrack(successes=1, failures=2)
    roller = _FixedRoller([20])
    res = roll_death_save(track, roller)
    assert res.healed_to == 1
    assert track.successes == 0 and track.failures == 0
    assert track.dead is False


def test_nat1_adds_two_failures() -> None:
    track = DeathSaveTrack()
    roller = _FixedRoller([1])
    roll_death_save(track, roller)
    assert track.failures == 2


def test_nat1_can_kill_outright() -> None:
    track = DeathSaveTrack(failures=1)
    roller = _FixedRoller([1])
    roll_death_save(track, roller)
    assert track.dead is True


def test_damage_at_zero_one_failure() -> None:
    track = DeathSaveTrack()
    damage_at_zero(track, crit=False)
    assert track.failures == 1


def test_critical_damage_at_zero_two_failures() -> None:
    track = DeathSaveTrack()
    damage_at_zero(track, crit=True)
    assert track.failures == 2


def test_damage_at_zero_can_kill() -> None:
    track = DeathSaveTrack(failures=2)
    damage_at_zero(track, crit=False)
    assert track.dead is True


def test_massive_damage_threshold() -> None:
    # SRD 5.2: damage ≥ 2 × max_hp at 0 HP → instant death.
    assert is_massive_damage(20, 10) is True
    assert is_massive_damage(19, 10) is False
    assert is_massive_damage(0, 10) is False
    assert is_massive_damage(100, 0) is False


def test_track_serialisation_round_trip() -> None:
    track = DeathSaveTrack(successes=2, failures=1)
    restored = DeathSaveTrack.from_dict(track.to_dict())
    assert restored == track

