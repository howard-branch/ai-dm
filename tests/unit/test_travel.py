"""Tests for travel pace, distance, forced march and TravelState."""
from __future__ import annotations

from ai_dm.game.combatant_state import CombatantState
from ai_dm.game.travel_state import TravelState
from ai_dm.rules import travel
from ai_dm.rules.dice import DiceRoller


class TestPaceCatalog:
    def test_slow_24_per_day(self):
        assert travel.pace("slow").per_day_mi == 18

    def test_normal_can_stealth_false(self):
        assert travel.can_stealth("normal") is False
        assert travel.can_stealth("slow") is True

    def test_fast_perception_penalty_minus_5(self):
        assert travel.perception_penalty("fast") == -5
        assert travel.perception_penalty("normal") == 0


class TestDistance:
    def test_normal_3_per_hour(self):
        assert travel.distance_per_hour_mi("normal") == 3

    def test_mounted_doubles_hourly(self):
        assert travel.distance_per_hour_mi("normal", mounted=True) == 6

    def test_difficult_terrain_halves_daily(self):
        assert travel.distance_per_day_mi("normal", terrain="difficult") == 12.0


class TestForcedMarch:
    def test_no_save_under_threshold(self):
        assert travel.forced_march_save_dc(8) is None

    def test_dc_scales_per_extra_hour(self):
        assert travel.forced_march_save_dc(9) == 11
        assert travel.forced_march_save_dc(11) == 13


class TestNavigation:
    def test_fast_pace_raises_dc_by_5(self):
        assert travel.navigation_dc(15, "fast") == 20
        assert travel.navigation_dc(15, "normal") == 15


class TestTravelState:
    def test_advance_accumulates_miles(self):
        ts = TravelState(pace="normal")
        result = ts.advance(2.0)
        assert result.miles_added == 6.0
        assert ts.total_miles == 6.0

    def test_advance_triggers_forced_march_after_threshold(self):
        ts = TravelState(pace="normal")
        actor = CombatantState(actor_id="a", name="A", saving_throws={"con": -10})
        ts.advance(8.0)  # exactly at threshold
        result = ts.advance(2.0, actors=[actor], roller=DiceRoller(seed=1))
        assert result.forced_march_triggered is True
        assert any(o.dc >= 11 for o in result.forced_march_outcomes)

    def test_navigation_check_marks_lost(self):
        ts = TravelState(pace="fast")
        ok = ts.make_navigation_check(15, navigator_total=10)
        assert ok is False
        assert ts.lost is True
        assert ts.last_navigation_dc == 20

    def test_round_trip(self):
        ts = TravelState(pace="slow", terrain="forest", mounted=True)
        ts.advance(1.0)
        again = TravelState.model_validate(ts.model_dump())
        assert again == ts

