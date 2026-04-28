"""Tests for sustenance (food/water/exhaustion) + interruptible rest progress."""
from __future__ import annotations

from ai_dm.game.combatant_state import CombatantState, Nourishment
from ai_dm.rules import rests, sustenance
from ai_dm.rules.dice import DiceRoller


class TestRationsAndWater:
    def test_medium_creature_needs_1_per_day(self):
        assert sustenance.rations_per_day_lb("medium") == 1.0
        assert sustenance.water_per_day_gal("medium") == 1.0

    def test_huge_creature_needs_16(self):
        assert sustenance.rations_per_day_lb("huge") == 16.0

    def test_hot_climate_doubles_water(self):
        assert sustenance.water_per_day_gal("medium", hot_climate=True) == 2.0


class TestStarvation:
    def test_grace_floor_one(self):
        # Con mod 0 → 1 day grace.
        assert sustenance.starvation_grace_days(0) == 1

    def test_grace_scales_with_con_mod(self):
        assert sustenance.starvation_grace_days(3) == 4

    def test_exhaustion_after_grace(self):
        c = CombatantState(actor_id="a", name="A", ability_mods={"con": 0})
        # 1 day = within grace.
        sustenance.tick_starvation(c, days_without_food=1)
        assert c.exhaustion == 0
        sustenance.tick_starvation(c, days_without_food=2)
        assert c.exhaustion == 1


class TestDehydration:
    def test_dc_starts_15(self):
        assert sustenance.dehydration_dc(0) == 15

    def test_dc_increases_per_failed_day(self):
        assert sustenance.dehydration_dc(2) == 25

    def test_failure_adds_exhaustion(self):
        c = CombatantState(actor_id="a", name="A", saving_throws={"con": -10})
        new, ok = sustenance.tick_dehydration(c, prev_failed_days=0, save_total=5)
        assert ok is False
        assert new == 1
        assert c.exhaustion == 1


class TestNourishmentRoundTrip:
    def test_round_trip_via_combatant_state(self):
        c = CombatantState(actor_id="a", name="A")
        c.nourishment.days_without_food = 3
        c.nourishment.eat_ration(at_minute=120)
        again = CombatantState.model_validate(c.model_dump())
        assert again.nourishment == c.nourishment
        assert again.nourishment.days_without_food == 0
        assert again.nourishment.last_meal_minute == 120

    def test_start_new_day_increments_counters(self):
        n = Nourishment()
        n.start_new_day(ate_today=False, drank_today=True)
        assert n.days_without_food == 1
        assert n.days_without_water == 0


class TestShortRestProgress:
    def test_completes_after_60_minutes(self):
        c = CombatantState(actor_id="a", name="A", hp=10, max_hp=20)
        p = rests.begin_short_rest(c)
        assert p.required_minutes() == 60
        assert p.advance(30) is False
        assert p.advance(30) is True
        # complete_rest applies the result and clears progress.
        result = rests.complete_rest(c)
        assert result is not None
        assert result.kind == "short"
        assert c.rest_progress is None

    def test_combat_interrupts_short_rest(self):
        c = CombatantState(actor_id="a", name="A")
        p = rests.begin_short_rest(c)
        p.advance(20)
        assert p.interrupt("combat", rounds=1) is True
        assert rests.complete_rest(c) is None


class TestLongRestProgress:
    def test_completes_after_8_hours(self):
        c = CombatantState(actor_id="a", name="A", hp=1, max_hp=20, exhaustion=2)
        p = rests.begin_long_rest(c)
        assert p.required_minutes() == 8 * 60
        p.advance(8 * 60)
        result = rests.complete_rest(c)
        assert result is not None
        assert result.kind == "long"
        assert c.hp == 20
        assert c.exhaustion == 1

    def test_strenuous_60min_interrupts_long_rest(self):
        c = CombatantState(actor_id="a", name="A")
        p = rests.begin_long_rest(c)
        p.advance(120)
        assert p.interrupt("strenuous", minutes=60) is True
        assert rests.complete_rest(c) is None


class TestRestProgressRoundTrip:
    def test_round_trip(self):
        c = CombatantState(actor_id="a", name="A")
        rests.begin_long_rest(c, started_minute=600)
        c.rest_progress.advance(120)  # type: ignore[union-attr]
        again = CombatantState.model_validate(c.model_dump())
        assert again.rest_progress == c.rest_progress


class TestDawnRecharge:
    def test_drives_inventory_dawn_recharge(self):
        c = CombatantState(actor_id="a", name="A")
        wand = c.inventory.give_magic_item("wand_of_magic_missiles")
        c.inventory.expend_charges(wand.instance_id, 7)
        result = rests.dawn_recharge(c, roller=DiceRoller(seed=1))
        assert wand.instance_id in result

