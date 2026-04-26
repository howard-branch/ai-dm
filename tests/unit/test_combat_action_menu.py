"""Tests for the extended combat-action menu (5e SRD).

Covers ``ActionResolver`` handlers for ``cast_spell``, ``dash``,
``disengage``, ``dodge``, ``help``, ``hide``, ``ready``, ``use_item``
and ``end_turn``, plus their bookkeeping on the canonical
``CombatantState``.
"""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from ai_dm.ai.intent_parser import IntentParser
from ai_dm.ai.intent_schemas import PlayerIntent
from ai_dm.game.combatant_state import CombatantState, Concentration, SpellSlot
from ai_dm.rules.action_resolver import ActionResolver
from ai_dm.rules.engine import ActorRuleState, RulesEngine
from ai_dm.rules.validators import validate_intent


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #


def _hero(**overrides) -> CombatantState:
    base = dict(actor_id="hero", name="Hero", hp=20, max_hp=20, team="party",
                speed=30)
    base.update(overrides)
    return CombatantState(**base)


def _resolver(actor: CombatantState | None = None, *, rules: RulesEngine | None = None):
    actors = {actor.actor_id: actor} if actor else {}

    def lookup(aid: str):
        return actors.get(aid)

    return ActionResolver(rules=rules, actor_lookup=lookup), actors


def _intent(kind: str, **fields) -> SimpleNamespace:
    fields.setdefault("actor_id", "hero")
    fields.setdefault("raw_text", kind)
    return SimpleNamespace(type=kind, **fields)


# --------------------------------------------------------------------- #
# Schema / validator coverage
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "kind",
    ["dash", "disengage", "dodge", "help", "hide", "ready", "end_turn",
     "cast_spell", "use_item", "attack"],
)
def test_validator_accepts_full_action_menu(kind: str) -> None:
    intent = PlayerIntent(type=kind, raw_text=kind, actor_id="hero",
                          target_id="goblin" if kind in ("attack", "help") else None,
                          skill=None)
    ok, reason = validate_intent(intent)
    assert ok, f"{kind!r} should validate (got {reason!r})"


# --------------------------------------------------------------------- #
# Dash / Disengage / Dodge — single-action effects
# --------------------------------------------------------------------- #


def test_dash_marks_actor_and_consumes_action() -> None:
    hero = _hero()
    resolver, _ = _resolver(hero)
    res = resolver.resolve_intent(_intent("dash"), ctx={})
    assert res.success and res.type == "dash"
    assert hero.dashed is True
    assert hero.action_used is True
    assert res.details["bonus_movement"] == hero.speed


def test_dash_fails_when_action_already_spent() -> None:
    hero = _hero(action_used=True)
    resolver, _ = _resolver(hero)
    res = resolver.resolve_intent(_intent("dash"), ctx={})
    assert res.success is False
    assert hero.dashed is False


def test_disengage_sets_flag() -> None:
    hero = _hero()
    resolver, _ = _resolver(hero)
    res = resolver.resolve_intent(_intent("disengage"), ctx={})
    assert res.success
    assert hero.disengaging is True
    assert hero.action_used is True


def test_dodge_sets_flag_and_clears_on_next_turn() -> None:
    hero = _hero()
    resolver, _ = _resolver(hero)
    resolver.resolve_intent(_intent("dodge"), ctx={})
    assert hero.dodging is True
    # Per RAW the effect ends at the start of your next turn.
    hero.start_of_turn()
    assert hero.dodging is False
    assert hero.action_used is False


# --------------------------------------------------------------------- #
# Help / Hide
# --------------------------------------------------------------------- #


def test_help_requires_target() -> None:
    hero = _hero()
    resolver, _ = _resolver(hero)
    res = resolver.resolve_intent(_intent("help", target_id=None), ctx={})
    assert res.success is False
    assert hero.helping_target is None
    assert hero.action_used is False


def test_help_records_target() -> None:
    hero = _hero()
    resolver, _ = _resolver(hero)
    res = resolver.resolve_intent(_intent("help", target_id="ally"), ctx={})
    assert res.success and res.target_id == "ally"
    assert hero.helping_target == "ally"


def test_hide_marks_actor_hidden_without_rules() -> None:
    hero = _hero()
    resolver, _ = _resolver(hero)
    res = resolver.resolve_intent(_intent("hide"), ctx={})
    assert res.success
    assert hero.hidden is True
    assert hero.action_used is True


def test_hide_runs_check_when_dc_provided() -> None:
    hero = _hero()
    rules = RulesEngine(rng=random.Random(0))
    resolver, _ = _resolver(hero, rules=rules)
    res = resolver.resolve_intent(_intent("hide"), ctx={"dc": 99, "stealth_modifier": 0})
    assert res.success is False
    assert hero.hidden is False
    # Action is still spent — the attempt happened.
    assert hero.action_used is True


def test_attacking_breaks_stealth() -> None:
    hero = _hero(hidden=True)
    rules = RulesEngine(rng=random.Random(0))
    resolver, actors = _resolver(hero, rules=rules)
    # The rules engine expects ActorRuleState-shaped targets (it reads
    # `resistances` etc.); use one here.
    actors["goblin"] = ActorRuleState(
        actor_id="goblin", name="Goblin", hp=5, max_hp=5, ac=10,
    )
    resolver.resolve_intent(
        _intent("attack", target_id="goblin"),
        ctx={"attack_modifier": 5, "damage_dice": "1d4"},
    )
    assert hero.hidden is False


# --------------------------------------------------------------------- #
# Ready
# --------------------------------------------------------------------- #


def test_ready_consumes_action_and_reaction() -> None:
    hero = _hero()
    resolver, _ = _resolver(hero)
    res = resolver.resolve_intent(
        _intent("ready"),
        ctx={"trigger": "when goblin opens door", "action": "attack"},
    )
    assert res.success
    assert hero.action_used is True
    assert hero.reaction_used is True
    assert hero.readied_action is not None
    assert hero.readied_action["action"] == "attack"
    assert "goblin" in hero.readied_action["trigger"]


def test_readied_spell_pre_spends_slot() -> None:
    hero = _hero(spell_slots={1: SpellSlot(level=1, current=1, max=1)})
    resolver, _ = _resolver(hero)
    res = resolver.resolve_intent(
        _intent("ready"),
        ctx={"trigger": "when foe steps in", "action": "cast_spell",
             "spell_level": 1, "payload": {"spell": "magic_missile"}},
    )
    assert res.success
    assert hero.spell_slots[1].current == 0


def test_ready_clears_at_start_of_next_turn() -> None:
    hero = _hero(readied_action={"trigger": "x", "action": "attack",
                                 "payload": {}, "spell_level": None},
                 reaction_used=True, action_used=True)
    hero.start_of_turn()
    assert hero.readied_action is None
    # Reaction reset is bound to start_of_round, not start_of_turn.
    assert hero.reaction_used is True


# --------------------------------------------------------------------- #
# Cast spell
# --------------------------------------------------------------------- #


def test_cast_spell_spends_slot_and_consumes_action() -> None:
    hero = _hero(spell_slots={1: SpellSlot(level=1, current=2, max=2)})
    resolver, _ = _resolver(hero)
    res = resolver.resolve_intent(
        _intent("cast_spell", spell="magic_missile", target_id="goblin"),
        ctx={"spell": "magic_missile", "level": 1},
    )
    assert res.success
    assert hero.spell_slots[1].current == 1
    assert hero.action_used is True


def test_cast_spell_no_slot_fails() -> None:
    hero = _hero(spell_slots={1: SpellSlot(level=1, current=0, max=2)})
    resolver, _ = _resolver(hero)
    res = resolver.resolve_intent(
        _intent("cast_spell", spell="bless"),
        ctx={"spell": "bless", "level": 1},
    )
    assert res.success is False
    assert hero.action_used is False


def test_cast_spell_bonus_action_path() -> None:
    hero = _hero(spell_slots={1: SpellSlot(level=1, current=1, max=1)})
    resolver, _ = _resolver(hero)
    res = resolver.resolve_intent(
        _intent("cast_spell", spell="healing_word"),
        ctx={"spell": "healing_word", "level": 1, "casting_time": "bonus"},
    )
    assert res.success
    assert hero.bonus_action_used is True
    assert hero.action_used is False


def test_cast_spell_concentration_starts() -> None:
    hero = _hero(spell_slots={1: SpellSlot(level=1, current=1, max=1)})
    resolver, _ = _resolver(hero)
    resolver.resolve_intent(
        _intent("cast_spell", spell="bless"),
        ctx={"spell": "bless", "level": 1, "concentration": True},
    )
    assert isinstance(hero.concentration, Concentration)
    assert hero.concentration.spell_id == "bless"


def test_cast_cantrip_does_not_spend_slot() -> None:
    hero = _hero(spell_slots={1: SpellSlot(level=1, current=1, max=1)})
    resolver, _ = _resolver(hero)
    res = resolver.resolve_intent(
        _intent("cast_spell", spell="fire_bolt"),
        ctx={"spell": "fire_bolt", "level": 0},
    )
    assert res.success
    assert hero.spell_slots[1].current == 1
    assert hero.action_used is True


# --------------------------------------------------------------------- #
# Use item / End turn
# --------------------------------------------------------------------- #


def test_use_item_default_consumes_action() -> None:
    hero = _hero()
    resolver, _ = _resolver(hero)
    res = resolver.resolve_intent(
        _intent("use_item", target_id="potion_of_healing"),
        ctx={},
    )
    assert res.success
    assert hero.action_used is True
    assert res.details["item"] == "potion_of_healing"


def test_use_item_bonus_path() -> None:
    hero = _hero()
    resolver, _ = _resolver(hero)
    resolver.resolve_intent(
        _intent("use_item", target_id="lantern"),
        ctx={"economy": "bonus"},
    )
    assert hero.bonus_action_used is True
    assert hero.action_used is False


def test_use_item_free_costs_nothing() -> None:
    hero = _hero()
    resolver, _ = _resolver(hero)
    resolver.resolve_intent(
        _intent("use_item", target_id="rope"),
        ctx={"economy": "free"},
    )
    assert hero.action_used is False
    assert hero.bonus_action_used is False


def test_end_turn_is_inert() -> None:
    hero = _hero()
    resolver, _ = _resolver(hero)
    res = resolver.resolve_intent(_intent("end_turn"), ctx={})
    assert res.success and res.type == "end_turn"
    # No action-economy change.
    assert (hero.action_used, hero.bonus_action_used, hero.reaction_used) == (
        False, False, False,
    )


# --------------------------------------------------------------------- #
# Intent parser fast-path coverage
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text,expected_type",
    [
        ("dash", "dash"),
        ("I dash", "dash"),
        ("disengage", "disengage"),
        ("I dodge", "dodge"),
        ("hide", "hide"),
        ("end turn", "end_turn"),
        ("done", "end_turn"),
        ("pass", "end_turn"),
    ],
)
def test_parser_recognises_combat_verbs(text: str, expected_type: str) -> None:
    parsed = IntentParser().parse(text)
    assert parsed.type == expected_type


def test_parser_help_with_target() -> None:
    parsed = IntentParser().parse("help vex")
    assert parsed.type == "help"
    assert parsed.target_id == "vex"


def test_parser_cast_with_target() -> None:
    parsed = IntentParser().parse("I cast magic missile at the goblin")
    assert parsed.type == "cast_spell"
    assert parsed.spell == "magic missile"
    assert parsed.target_id == "goblin"


def test_parser_ready_records_trigger() -> None:
    parsed = IntentParser().parse("ready attack when the door opens")
    assert parsed.type == "ready"
    assert parsed.extra.get("action") == "attack"
    assert "door" in parsed.extra.get("trigger", "")

