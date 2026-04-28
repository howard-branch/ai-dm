"""Tests for the machine-actionable Condition model + tracker."""
from __future__ import annotations

import pytest

from ai_dm.game.combatant_state import CombatantState
from ai_dm.game.condition_instance import (
    ConcentrationLink,
    ConditionInstance,
    SaveToEnd,
    coerce_instance,
    key_of,
)
from ai_dm.game.condition_tracker import (
    ConditionTracker,
    cascade_actor_died,
    cascade_concentration_dropped,
)
from ai_dm.rules import condition_predicates as cp
from ai_dm.rules.conditions import implied


def _c(**kw) -> CombatantState:
    return CombatantState(actor_id=kw.pop("actor_id", "a"),
                          name=kw.pop("name", "Actor"),
                          **kw)


# --------------------------------------------------------------------- #
# ConditionInstance schema
# --------------------------------------------------------------------- #


class TestConditionInstance:
    def test_minimum_construct(self):
        inst = ConditionInstance(key="poisoned")
        assert inst.key == "poisoned"
        assert inst.source == "unknown"
        assert inst.expires_on == "never"
        assert inst.id  # uuid populated

    def test_extra_forbid(self):
        with pytest.raises(Exception):
            ConditionInstance(key="poisoned", bogus=1)

    def test_key_normalisation(self):
        inst = ConditionInstance(key="  POISONED  ")
        assert inst.key == "poisoned"

    def test_round_trip(self):
        inst = ConditionInstance(
            key="frightened",
            source="actor:goblin",
            duration_rounds=10,
            save_to_end=SaveToEnd(ability="wis", dc=12),
            concentration_link=ConcentrationLink(caster_id="goblin", spell_id="fear"),
        )
        d = inst.model_dump()
        again = ConditionInstance.model_validate(d)
        assert again == inst

    def test_supersedes_longer_wins(self):
        a = ConditionInstance(key="poisoned", duration_rounds=2)
        b = ConditionInstance(key="poisoned", duration_rounds=10)
        assert b.supersedes(a)
        assert not a.supersedes(b)

    def test_permanent_beats_timed(self):
        timed = ConditionInstance(key="poisoned", duration_rounds=5)
        perm = ConditionInstance(key="poisoned")
        assert perm.supersedes(timed)
        assert not timed.supersedes(perm)


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #


def test_coerce_instance_from_str():
    inst = coerce_instance("Poisoned ")
    assert inst is not None
    assert inst.key == "poisoned"
    assert inst.source == "legacy"


def test_coerce_instance_from_dict():
    inst = coerce_instance({"key": "blinded", "source": "spell:blindness"})
    assert inst is not None
    assert inst.source == "spell:blindness"


def test_coerce_instance_drops_garbage():
    assert coerce_instance(None) is None
    assert coerce_instance("") is None


def test_key_of():
    assert key_of("Stunned") == "stunned"
    assert key_of(ConditionInstance(key="prone")) == "prone"
    assert key_of({"key": "Charmed"}) == "charmed"
    assert key_of(42) is None


# --------------------------------------------------------------------- #
# CombatantState integration
# --------------------------------------------------------------------- #


class TestCombatantStateConditions:
    def test_legacy_string_list_promoted(self):
        c = _c(conditions=["poisoned", "Prone"])
        assert all(isinstance(x, ConditionInstance) for x in c.conditions)
        assert c.condition_keys() == {"poisoned", "prone"}

    def test_dict_payload_promoted(self):
        c = _c(conditions=[{"key": "frightened", "source": "spell:fear"}])
        assert c.conditions[0].source == "spell:fear"

    def test_has_condition_with_implied(self):
        c = _c()
        c.add_condition("paralyzed", source="spell:hold_person")
        # Paralyzed implies incapacitated.
        assert c.has_condition("incapacitated")
        assert c.has_condition("paralyzed")

    def test_unconscious_implies_prone(self):
        c = _c()
        c.add_condition("unconscious", source="hp_zero")
        assert c.has_condition("prone")
        assert c.has_condition("incapacitated")

    def test_immunity_blocks_apply(self):
        c = _c(condition_immunities=["poisoned"])
        result = c.add_condition("poisoned", source="spell:foo")
        assert result is None
        assert not c.has_condition("poisoned")

    def test_dedupe_by_key_and_source(self):
        c = _c()
        c.add_condition("poisoned", source="goblin", duration_rounds=2)
        c.add_condition("poisoned", source="goblin", duration_rounds=10)
        # Same (key, source) — only one instance retained, longer wins.
        poisons = [i for i in c.conditions if i.key == "poisoned"]
        assert len(poisons) == 1
        assert poisons[0].duration_rounds == 10

    def test_two_sources_kept_independent(self):
        c = _c()
        c.add_condition("frightened", source="goblin_a")
        c.add_condition("frightened", source="goblin_b")
        assert sum(1 for i in c.conditions if i.key == "frightened") == 2

    def test_remove_specific_source(self):
        c = _c()
        c.add_condition("frightened", source="goblin_a")
        c.add_condition("frightened", source="goblin_b")
        c.remove_condition("frightened", source="goblin_a")
        keep = [i for i in c.conditions if i.key == "frightened"]
        assert len(keep) == 1
        assert keep[0].source == "goblin_b"

    def test_remove_implication_only_clears_own(self):
        c = _c()
        c.add_condition("prone", source="manual")  # pre-existing
        c.add_condition("unconscious", source="hp_zero")
        # Removing unconscious should NOT take the manual prone with it.
        c.remove_condition("unconscious")
        assert c.has_condition("prone")

    def test_heal_from_zero_drops_only_hp_zero_unconscious(self):
        c = _c(hp=0, max_hp=10)
        c.add_condition("unconscious", source="hp_zero")
        c.add_condition("unconscious", source="spell:sleep")
        c.heal(5)
        keys_sources = {(i.key, i.source) for i in c.conditions}
        assert ("unconscious", "spell:sleep") in keys_sources
        assert ("unconscious", "hp_zero") not in keys_sources


# --------------------------------------------------------------------- #
# Tracker lifecycle
# --------------------------------------------------------------------- #


class TestConditionTrackerLifecycle:
    def test_expires_on_end_of_turn(self):
        c = _c()
        c.add_condition("frightened", source="x", expires_on="end_of_target_turn")
        ConditionTracker(c).tick_end_of_turn(current_round=1)
        assert not c.has_condition("frightened")

    def test_expires_at_round(self):
        c = _c()
        c.add_condition("blinded", source="x", expires_at_round=3)
        ConditionTracker(c).tick_start_of_turn(current_round=2)
        assert c.has_condition("blinded")
        ConditionTracker(c).tick_start_of_turn(current_round=3)
        assert not c.has_condition("blinded")

    def test_save_to_end_success_drops(self):
        c = _c()
        c.add_condition(
            "paralyzed",
            source="spell:hold_person",
            expires_on="end_of_target_turn",
            save_to_end=SaveToEnd(ability="wis", dc=10, when="end_of_turn"),
        )

        class _AlwaysHigh:
            def d20(self, *, bonus=0, advantage=False, disadvantage=False):
                class R: total = 20
                return R()

        ConditionTracker(c).tick_end_of_turn(
            current_round=1,
            roller=_AlwaysHigh(),
            saves={"wis": 0},
        )
        assert not c.has_condition("paralyzed")
        assert not c.has_condition("incapacitated")  # implication removed too

    def test_save_to_end_failure_keeps(self):
        c = _c()
        c.add_condition(
            "paralyzed",
            source="spell:hold_person",
            save_to_end=SaveToEnd(ability="wis", dc=20, when="end_of_turn"),
        )

        class _AlwaysLow:
            def d20(self, *, bonus=0, advantage=False, disadvantage=False):
                class R: total = 1
                return R()

        ConditionTracker(c).tick_end_of_turn(
            current_round=1,
            roller=_AlwaysLow(),
            saves={"wis": 0},
        )
        assert c.has_condition("paralyzed")


# --------------------------------------------------------------------- #
# Cascades
# --------------------------------------------------------------------- #


class TestCascades:
    def test_concentration_drop_clears_linked(self):
        victim = _c(actor_id="v")
        victim.add_condition(
            "frightened",
            source="spell:fear",
            concentration_link=ConcentrationLink(caster_id="caster", spell_id="fear"),
        )
        victim.add_condition("poisoned", source="other")  # unrelated
        n = cascade_concentration_dropped("caster", [victim])
        assert n == 1
        assert not victim.has_condition("frightened")
        assert victim.has_condition("poisoned")

    def test_actor_death_cascades(self):
        victim = _c(actor_id="v")
        victim.add_condition(
            "paralyzed",
            source="spell:hold_person",
            concentration_link=ConcentrationLink(caster_id="caster", spell_id="hold_person"),
        )
        cascade_actor_died("caster", [victim])
        assert not victim.has_condition("paralyzed")
        assert not victim.has_condition("incapacitated")


# --------------------------------------------------------------------- #
# Predicates
# --------------------------------------------------------------------- #


class TestPredicates:
    def test_can_take_actions(self):
        assert cp.can_take_actions([])
        assert not cp.can_take_actions(["incapacitated"])
        assert not cp.can_take_actions(["paralyzed"])  # implies incapacitated
        assert not cp.can_take_actions([ConditionInstance(key="stunned")])

    def test_speed_zero(self):
        assert cp.speed_zero(["grappled"])
        assert cp.speed_zero(["restrained"])
        assert not cp.speed_zero(["blinded"])

    def test_auto_fail_str_dex_saves(self):
        assert cp.auto_fail_str_dex_saves(["paralyzed"])
        assert cp.auto_fail_str_dex_saves(["unconscious"])
        assert not cp.auto_fail_str_dex_saves(["poisoned"])

    def test_crit_within_5ft(self):
        assert cp.crit_within_5ft(["paralyzed"])
        assert cp.crit_within_5ft(["unconscious"])
        assert not cp.crit_within_5ft(["prone"])

    def test_cannot_attack_charmer(self):
        assert cp.cannot_attack(["charmed"], target_id="bob", charmer_id="bob")
        assert not cp.cannot_attack(["charmed"], target_id="bob", charmer_id="alice")
        assert not cp.cannot_attack([], target_id="bob", charmer_id="bob")

    def test_cannot_move_closer_when_frightened(self):
        assert cp.cannot_move_closer_to(
            ["frightened"], source_id="dragon", fear_source_id="dragon")
        assert not cp.cannot_move_closer_to(
            ["frightened"], source_id="goblin", fear_source_id="dragon")


def test_implied_accepts_condition_instances():
    insts = [ConditionInstance(key="unconscious")]
    assert "prone" in implied(insts)
    assert "incapacitated" in implied(insts)

