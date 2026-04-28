"""Tests for the new typed rules glossary (Steps 1-6 vertical slice).

These guard:
* Pydantic discriminated-union validation (effects.py).
* Registry lookup, search, raw_record (registry.py).
* Compat parity: synthesised legacy dict matches the catalog's
  ``effects_legacy`` for every condition.
* Round-trip through the typed effects of Prone / Grappled / Restrained.
"""
from __future__ import annotations

import pytest

from ai_dm.rules.conditions import ALL_CONDITIONS
from ai_dm.rules.glossary import (
    Condition,
    Effect,
    legacy_effects,
    registry,
    synthesise_legacy,
)
from ai_dm.rules.glossary import effects as E
from ai_dm.rules.glossary.models import GlossaryEntry


# --------------------------------------------------------------------- #
# Models / discriminated union
# --------------------------------------------------------------------- #


class TestEffectsUnion:
    def test_roundtrip_each_concrete_effect(self):
        samples: list[E.Effect] = [
            E.MovementCost(value="crawl"),
            E.SpeedSetTo(value=0),
            E.OwnAttackAdvantage(),
            E.AttackAdvantageAgainst(range="melee_5ft"),
            E.AttackDisadvantageAgainst(range="ranged_beyond_5ft"),
            E.SaveAutoFail(abilities=("str", "dex")),
            E.SaveDisadvantage(abilities=("dex",)),
            E.AbilityCheckDisadvantage(),
            E.AutoFailCheckWithSense(sense="sight"),
            E.ImpliesCondition(condition="incapacitated"),
            E.AutoCriticalHitAgainstWithin5ft(),
            E.DamageResistanceAll(),
            E.ConditionImmunity(conditions=("poisoned",)),
            E.D20PenaltyPerLevel(value=-2),
            E.DeathAtMaxStack(),
        ]
        # Wrap each one in a tuple-typed entry to exercise the union.
        for ef in samples:
            entry = GlossaryEntry(key="x", label="X", effects=(ef,))
            again = GlossaryEntry.model_validate(entry.model_dump())
            assert again.effects[0] == ef

    def test_unknown_effect_type_rejected(self):
        with pytest.raises(Exception):
            GlossaryEntry.model_validate(
                {"key": "x", "label": "X", "effects": [{"type": "no_such_thing"}]}
            )

    def test_speed_set_to_rejects_negative(self):
        with pytest.raises(Exception):
            E.SpeedSetTo(value=-1)


# --------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------- #


class TestRegistry:
    def test_get_condition_returns_typed_entry(self):
        prone = registry.get_condition("prone")
        assert isinstance(prone, Condition)
        assert prone.label == "Prone"
        assert "Prone" in prone.rules_text

    def test_lookup_is_case_insensitive(self):
        assert registry.get_condition("PRONE") is not None
        assert registry.get_condition("Grappled") is not None

    def test_unknown_returns_none(self):
        assert registry.get_condition("not_a_condition") is None

    def test_all_conditions_loaded(self):
        keys = {c.key for c in registry.all("conditions")}
        assert keys == set(ALL_CONDITIONS)
        assert len(keys) == 15

    def test_raw_record_exposes_effects_legacy(self):
        raw = registry.raw_record("conditions", "blinded")
        assert raw is not None
        assert raw["effects_legacy"]["target_advantage"] is True

    def test_search_finds_by_label_and_text(self):
        hits = registry.search("Prone")
        assert any(e.key == "prone" for e in hits)
        # Substring in rules_text only:
        hits2 = registry.search("crawl")
        assert any(e.key == "prone" for e in hits2)


# --------------------------------------------------------------------- #
# Compat parity — the most important guard during migration
# --------------------------------------------------------------------- #


class TestLegacyParity:
    @pytest.mark.parametrize("key", ALL_CONDITIONS)
    def test_synthesised_matches_effects_legacy(self, key: str):
        raw = registry.raw_record("conditions", key)
        assert raw is not None
        expected = raw.get("effects_legacy") or {}
        # legacy_effects() prefers typed → synthesise → compare to authored dict.
        produced = legacy_effects(key)
        assert produced == expected, f"parity drift on {key}: {produced!r} != {expected!r}"

    def test_synthesise_legacy_direct(self):
        out = synthesise_legacy(
            [
                E.OwnAttackDisadvantage(),
                E.AttackAdvantageAgainst(range="melee_5ft"),
                E.AttackDisadvantageAgainst(range="ranged_beyond_5ft"),
            ]
        )
        assert out == {
            "attacker_disadvantage": True,
            "target_advantage_melee": True,
            "target_disadvantage_ranged": True,
        }


# --------------------------------------------------------------------- #
# Typed-effect coverage for the canonical "physical restraint" set
# --------------------------------------------------------------------- #


def _types(entry: Condition) -> set[str]:
    return {e.type for e in entry.effects}


class TestKeyConditions:
    def test_prone_typed_effects(self):
        prone = registry.get_condition("prone")
        assert prone is not None
        assert _types(prone) == {
            "own_attack_disadvantage",
            "attack_advantage_against",
            "attack_disadvantage_against",
        }

    def test_grappled_speed_zero(self):
        g = registry.get_condition("grappled")
        assert g is not None
        assert any(
            isinstance(e, E.SpeedSetTo) and e.value == 0 for e in g.effects
        )

    def test_restrained_dex_save_disadvantage(self):
        r = registry.get_condition("restrained")
        assert r is not None
        assert any(
            isinstance(e, E.SaveDisadvantage) and "dex" in e.abilities
            for e in r.effects
        )

    def test_unconscious_implies_prone_and_incapacitated(self):
        u = registry.get_condition("unconscious")
        assert u is not None
        implied = {
            e.condition for e in u.effects if isinstance(e, E.ImpliesCondition)
        }
        assert implied == {"prone", "incapacitated"}


# --------------------------------------------------------------------- #
# Effect: Effect public alias is iterable in the typed union
# --------------------------------------------------------------------- #


def test_effect_alias_is_exported():
    assert Effect is not None

