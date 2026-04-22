import random

import pytest

from ai_dm.game.combat_machine import CombatMachine, InvalidCombatTransition
from ai_dm.game.combat_state import Participant
from ai_dm.orchestration.event_bus import EventBus


def _make() -> CombatMachine:
    bus = EventBus()
    return CombatMachine(event_bus=bus, command_router=None, rng=random.Random(0))


def _participants() -> list[Participant]:
    return [
        Participant(actor_id="hero", name="Hero", hp=20, max_hp=20, team="party"),
        Participant(actor_id="goblin", name="Goblin", hp=8, max_hp=8, team="foe"),
    ]


def test_full_happy_path():
    cm = _make()
    cm.start_encounter("enc1", _participants())
    cm.roll_initiative()
    assert cm.state.phase == "rolling_initiative"
    cm.begin_round()
    assert cm.state.phase == "in_round"
    actor = cm.request_action()
    assert cm.state.phase == "awaiting_action"
    cm.submit_action("attack", {"target": "goblin"}, {"hit": True})
    assert cm.state.phase == "resolving_action"
    nxt = cm.end_turn()
    assert cm.state.phase == "in_round"
    assert nxt is not None  # second participant
    cm.request_action()
    cm.submit_action("attack", {"target": "hero"}, {"hit": False})
    assert cm.end_turn() is None  # round complete
    cm.end_encounter("victory")
    assert cm.state.phase == "ended"


def test_illegal_transition_raises():
    cm = _make()
    cm.start_encounter("enc", _participants())
    with pytest.raises(InvalidCombatTransition):
        cm.submit_action("attack")  # no awaiting_action yet


def test_initiative_sorted_descending():
    cm = _make()
    p1 = Participant(actor_id="a", name="A", initiative=5)
    p2 = Participant(actor_id="b", name="B", initiative=15)
    cm.start_encounter("e", [p1, p2])
    order = cm.roll_initiative()
    assert order[0].actor_id == "b"


def test_phase_changed_events_published():
    bus = EventBus()
    cm = CombatMachine(event_bus=bus, command_router=None)
    cm.start_encounter("e", _participants())
    cm.roll_initiative()
    cm.begin_round()
    cm.request_action()
    transitions = [
        (p["from"], p["to"]) for n, p in bus.history if n == "combat.phase_changed"
    ]
    assert ("idle", "rolling_initiative") in transitions
    assert ("rolling_initiative", "in_round") in transitions
    assert ("in_round", "awaiting_action") in transitions


def test_snapshot_round_trip():
    cm = _make()
    cm.start_encounter("e", _participants())
    cm.roll_initiative()
    snap = cm.snapshot()

    cm2 = _make()
    cm2.restore(snap)
    assert cm2.state is not None
    assert cm2.state.encounter_id == "e"
    assert cm2.state.phase == "rolling_initiative"

