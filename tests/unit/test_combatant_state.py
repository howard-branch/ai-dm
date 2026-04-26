"""Tests for the canonical Python-side CombatantState."""
from __future__ import annotations

import random

import pytest

from ai_dm.app.bootstrap import _migrate_spell_block
from ai_dm.app.character_wizard import build_sheet
from ai_dm.game.combat_machine import CombatMachine
from ai_dm.game.combat_state import CombatState, Participant
from ai_dm.game.combatant_state import (
    CombatantState,
    Concentration,
    Position,
    ResourceUse,
    SpellSlot,
)
from ai_dm.orchestration.event_bus import EventBus


# --------------------------------------------------------------------- #
# Defaults & shape
# --------------------------------------------------------------------- #


def test_defaults_cover_every_required_field() -> None:
    c = CombatantState(actor_id="a", name="A")
    # Vitals
    assert (c.hp, c.max_hp, c.temp_hp, c.ac) == (0, 0, 0, 10)
    # Geometry & status
    assert c.position is None
    assert c.conditions == []
    assert c.concentration is None
    # Action economy starts unspent.
    assert c.action_used is False
    assert c.bonus_action_used is False
    assert c.reaction_used is False
    assert c.movement_used == 0
    # Resource pools / slots / spells default empty.
    assert c.resources == {}
    assert c.spell_slots == {}
    assert c.cantrips == c.known_spells == c.prepared_spells == []
    # Versioning stamped.
    assert c.schema_version == 2


def test_participant_alias_is_combatant_state() -> None:
    """The legacy import path stays valid."""
    assert Participant is CombatantState
    p = Participant(actor_id="hero", name="Hero", hp=20, max_hp=20, team="party")
    assert isinstance(p, CombatantState)
    assert p.controller == "ai"  # default


def test_extra_fields_rejected() -> None:
    with pytest.raises(Exception):
        CombatantState(actor_id="a", name="A", junk_field=1)


# --------------------------------------------------------------------- #
# Reset semantics
# --------------------------------------------------------------------- #


def test_start_of_turn_resets_per_turn_economy() -> None:
    c = CombatantState(
        actor_id="a", name="A",
        action_used=True, bonus_action_used=True, reaction_used=True,
        movement_used=15,
        resources={
            "ki": ResourceUse(key="ki", current=0, max=4, recharge="turn"),
            "rage": ResourceUse(key="rage", current=0, max=2, recharge="long"),
        },
    )
    c.start_of_turn()
    assert c.action_used is False
    assert c.bonus_action_used is False
    assert c.movement_used == 0
    # Reactions persist across turns within a round.
    assert c.reaction_used is True
    # Per-turn resources refresh; per-long-rest resources do not.
    assert c.resources["ki"].current == 4
    assert c.resources["rage"].current == 0


def test_start_of_round_resets_reaction_and_round_resources() -> None:
    c = CombatantState(
        actor_id="a", name="A",
        reaction_used=True,
        resources={
            "legendary": ResourceUse(key="legendary", current=0, max=3, recharge="round"),
            "rage": ResourceUse(key="rage", current=0, max=2, recharge="long"),
        },
    )
    c.start_of_round()
    assert c.reaction_used is False
    assert c.resources["legendary"].current == 3
    assert c.resources["rage"].current == 0


def test_end_encounter_clears_concentration_and_economy() -> None:
    c = CombatantState(
        actor_id="a", name="A",
        action_used=True, bonus_action_used=True, reaction_used=True,
        movement_used=30,
        concentration=Concentration(spell_id="bless"),
        resources={
            "channel": ResourceUse(key="channel", current=0, max=1, recharge="encounter"),
        },
    )
    c.end_encounter()
    assert c.concentration is None
    assert (c.action_used, c.bonus_action_used, c.reaction_used) == (False, False, False)
    assert c.movement_used == 0
    assert c.resources["channel"].current == 1


# --------------------------------------------------------------------- #
# Convenience mutators
# --------------------------------------------------------------------- #


def test_take_damage_eats_temp_hp_first() -> None:
    c = CombatantState(actor_id="a", name="A", hp=10, max_hp=10, temp_hp=5)
    new_hp = c.take_damage(7)
    assert c.temp_hp == 0
    assert new_hp == c.hp == 8


def test_take_damage_to_zero_drops_concentration() -> None:
    c = CombatantState(
        actor_id="a", name="A", hp=3, max_hp=10,
        concentration=Concentration(spell_id="bless"),
    )
    c.take_damage(50)
    assert c.hp == 0
    assert c.concentration is None


def test_heal_clamps_to_max() -> None:
    c = CombatantState(actor_id="a", name="A", hp=4, max_hp=10)
    assert c.heal(99) == 10


def test_spend_slot_decrements_and_refuses_when_empty() -> None:
    c = CombatantState(
        actor_id="a", name="A",
        spell_slots={1: SpellSlot(level=1, current=2, max=2)},
    )
    assert c.spend_slot(1) is True
    assert c.spell_slots[1].current == 1
    assert c.spend_slot(1, n=5) is False  # not enough left
    assert c.spell_slots[1].current == 1
    # Unknown level returns False, never raises.
    assert c.spend_slot(9) is False


# --------------------------------------------------------------------- #
# Builders: from_pc_sheet
# --------------------------------------------------------------------- #


def test_from_pc_sheet_via_witch_build() -> None:
    sheet = build_sheet(
        "m", "Morgana", "witch", None, "forbidden_scholar",
        spell_picks=["hex"],
    )
    c = CombatantState.from_pc_sheet(sheet, token_id="tok_m")
    assert c.actor_id == "m"
    assert c.name == "Morgana"
    assert c.token_id == "tok_m"
    assert c.team == "party"
    assert c.controller == "player"
    assert c.source == "pc"
    assert c.hp > 0 and c.hp == c.max_hp
    assert c.ac >= 10
    assert c.speed == 30
    # Witch is a "known" caster — known_spells populated, prepared_spells empty.
    assert "hex" in c.known_spells
    assert c.prepared_spells == []
    # Slot pools materialised from the spellcasting block.
    assert all(isinstance(v, SpellSlot) for v in c.spell_slots.values())
    assert c.spell_slots[1].current == c.spell_slots[1].max > 0


def test_from_pc_sheet_via_scholar_build_uses_prepared_ids() -> None:
    sheet = build_sheet(
        "s", "Sage", "scholar", None, "forbidden_scholar",
        spell_picks=["magic_missile"],
    )
    c = CombatantState.from_pc_sheet(sheet)
    assert "magic_missile" in c.prepared_spells
    assert c.known_spells == []  # prepared family


def test_from_pc_sheet_handles_legacy_then_migrated_sheet() -> None:
    legacy = {
        "id": "s", "name": "S", "class": "Scholar",
        "hp": {"current": 7, "max": 9, "temp": 2}, "ac": 13, "speed": 25,
        "conditions": ["prone"],
        "spells": {
            "cantrips_known": [{"id": "light"}],
            "prepared": [{"id": "shield", "level": 1}],
            "known": [{"id": "shield", "level": 1}],
            "slots": {"1": {"max": 2, "value": 1}},
        },
    }
    migrated = _migrate_spell_block(legacy)
    c = CombatantState.from_pc_sheet(migrated)
    assert (c.hp, c.max_hp, c.temp_hp) == (7, 9, 2)
    assert c.ac == 13
    assert c.speed == 25
    assert c.conditions == ["prone"]
    assert c.cantrips == ["light"]
    # Scholar is "prepared" — migration moved the v1 list into spellbook
    # and kept it as the prepared subset; from_pc_sheet pulls ids.
    assert c.prepared_spells == ["shield"]
    assert c.spell_slots[1].max == 2
    assert c.spell_slots[1].current == 1


def test_from_pc_sheet_pulls_resources_from_features() -> None:
    sheet = {
        "id": "f", "name": "F",
        "hp": {"current": 10, "max": 10}, "ac": 15,
        "abilities_features": [
            {"key": "second_wind", "name": "Second Wind",
             "uses": {"max": 1, "recharge": "short"}},
            {"key": "passive", "name": "Passive"},  # no uses → skipped
        ],
    }
    c = CombatantState.from_pc_sheet(sheet)
    assert "second_wind" in c.resources
    sw = c.resources["second_wind"]
    assert sw.max == 1 and sw.current == 1 and sw.recharge == "short"
    assert "passive" not in c.resources


# --------------------------------------------------------------------- #
# Builders: from_npc_block
# --------------------------------------------------------------------- #


def test_from_npc_block_flat_shape() -> None:
    block = {
        "id": "gob1", "name": "Goblin",
        "hp": 7, "max_hp": 7, "ac": 15, "speed": 30,
        "initiative_bonus": 2,
        "position": {"x": 100, "y": 200, "scene_id": "cave"},
        "conditions": ["hidden"],
    }
    c = CombatantState.from_npc_block(block, token_id="tok_g")
    assert c.team == "foe"
    assert c.controller == "ai"
    assert c.source == "npc"
    assert c.token_id == "tok_g"
    assert c.hp == 7 and c.max_hp == 7
    assert c.initiative_bonus == 2
    assert isinstance(c.position, Position)
    assert (c.position.x, c.position.y, c.position.scene_id) == (100, 200, "cave")


def test_from_npc_block_sheet_shape_hp() -> None:
    block = {"id": "x", "name": "X", "hp": {"current": 4, "max": 9}, "ac": 12}
    c = CombatantState.from_npc_block(block)
    assert c.hp == 4 and c.max_hp == 9


# --------------------------------------------------------------------- #
# CombatMachine integration: lifecycle calls reset hooks
# --------------------------------------------------------------------- #


def _machine() -> CombatMachine:
    return CombatMachine(
        event_bus=EventBus(), command_router=None, rng=random.Random(0),
    )


def test_request_action_resets_active_actor_economy() -> None:
    cm = _machine()
    hero = CombatantState(
        actor_id="hero", name="Hero", hp=10, max_hp=10, team="party",
        action_used=True, bonus_action_used=True, movement_used=30,
    )
    cm.start_encounter("e", [hero])
    cm.roll_initiative()
    cm.begin_round()
    actor = cm.request_action()
    assert actor.action_used is False
    assert actor.bonus_action_used is False
    assert actor.movement_used == 0


def test_begin_round_resets_reactions_for_all() -> None:
    cm = _machine()
    a = CombatantState(actor_id="a", name="A", reaction_used=True, team="party")
    b = CombatantState(actor_id="b", name="B", reaction_used=True, team="foe")
    cm.start_encounter("e", [a, b])
    cm.roll_initiative()
    cm.begin_round()
    assert all(p.reaction_used is False for p in cm.state.participants)


def test_end_encounter_drops_concentration_for_all() -> None:
    cm = _machine()
    caster = CombatantState(
        actor_id="c", name="C", team="party",
        concentration=Concentration(spell_id="bless"),
    )
    cm.start_encounter("e", [caster])
    cm.roll_initiative()
    cm.begin_round()
    cm.request_action()
    cm.submit_action("cast", {"spell": "bless"})
    cm.end_turn()
    cm.end_encounter("victory")
    assert cm.state.participants[0].concentration is None


# --------------------------------------------------------------------- #
# Snapshot round-trip preserves the new fields
# --------------------------------------------------------------------- #


def test_snapshot_round_trip_preserves_canonical_state() -> None:
    cm = _machine()
    hero = CombatantState(
        actor_id="hero", name="Hero", hp=12, max_hp=20, ac=16, team="party",
        position=Position(x=50, y=50, scene_id="start"),
        spell_slots={1: SpellSlot(level=1, current=1, max=2)},
        prepared_spells=["shield"],
        resources={"sw": ResourceUse(key="sw", current=0, max=1, recharge="short")},
        concentration=Concentration(spell_id="bless"),
    )
    cm.start_encounter("e", [hero])
    snap = cm.snapshot()

    cm2 = _machine()
    cm2.restore(snap)
    p = cm2.state.participants[0]
    assert p.position == Position(x=50, y=50, scene_id="start")
    assert p.spell_slots[1] == SpellSlot(level=1, current=1, max=2)
    assert p.prepared_spells == ["shield"]
    assert p.resources["sw"].max == 1
    assert p.concentration and p.concentration.spell_id == "bless"
    assert p.schema_version == 2


def test_combat_state_typing_accepts_dict_inputs_via_validation() -> None:
    """``CombatState.model_validate`` happily promotes plain dicts."""
    cs = CombatState.model_validate({
        "encounter_id": "e",
        "participants": [
            {"actor_id": "a", "name": "A", "hp": 5, "max_hp": 5},
        ],
    })
    assert isinstance(cs.participants[0], CombatantState)

