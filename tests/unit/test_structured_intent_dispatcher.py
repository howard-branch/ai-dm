"""Unit tests for the structured-intent dispatcher (Phase 3)."""
from __future__ import annotations

import random

from ai_dm.game.combat_machine import CombatMachine
from ai_dm.game.combat_state import Participant
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.orchestration.structured_intent_dispatcher import StructuredIntentDispatcher


class _StubClient:
    def __init__(self):
        self.events = []

    def send_event(self, name, payload, **_):
        self.events.append((name, payload))


class _StubRouter:
    def __init__(self):
        self.handled = []

    def handle(self, intent, ctx=None):
        self.handled.append((intent, ctx))
        from ai_dm.ai.intent_router import IntentEnvelope
        return IntentEnvelope(intent=intent)


def test_attack_intent_routed_to_intent_router():
    bus = EventBus()
    router = _StubRouter()
    client = _StubClient()
    StructuredIntentDispatcher(
        event_bus=bus, intent_router=router, combat=None, client=client,
    ).start()
    bus.publish("foundry.player_intent", {
        "type": "attack", "actor_id": "hero", "target_id": "goblin",
        "scene_id": "s1", "user_id": "u1",
    })
    assert len(router.handled) == 1
    intent, ctx = router.handled[0]
    assert intent.type == "attack"
    assert intent.actor_id == "hero"
    assert ctx == {"scene_id": "s1"}


def test_end_turn_drives_combat_machine():
    bus = EventBus()
    combat = CombatMachine(event_bus=bus, rng=random.Random(1), auto_highlight=False)
    combat.start_encounter("e1", [
        Participant(actor_id="hero", name="Hero"),
        Participant(actor_id="goblin", name="Goblin"),
    ])
    combat.roll_initiative()
    combat.begin_round()
    combat.request_action()

    client = _StubClient()
    StructuredIntentDispatcher(
        event_bus=bus, intent_router=None, combat=combat, client=client,
    ).start()

    bus.publish("foundry.player_intent", {
        "type": "end_turn", "actor_id": "hero",
    })
    # CombatMachine advanced the turn.
    assert combat.state.current_index == 1
    # Narration pushed.
    assert client.events
    name, payload = client.events[-1]
    assert name == "narration"
    assert "end turn" in payload["narration"].lower()


def test_dash_action_logged_in_combat():
    bus = EventBus()
    combat = CombatMachine(event_bus=bus, rng=random.Random(1), auto_highlight=False)
    combat.start_encounter("e1", [Participant(actor_id="hero", name="Hero")])
    combat.roll_initiative()
    combat.begin_round()
    combat.request_action()
    StructuredIntentDispatcher(
        event_bus=bus, intent_router=None, combat=combat, client=_StubClient(),
    ).start()
    bus.publish("foundry.player_intent", {"type": "dash", "actor_id": "hero"})
    assert combat.state.log[-1].kind == "dash"


def test_unknown_verb_is_ignored():
    bus = EventBus()
    StructuredIntentDispatcher(
        event_bus=bus, intent_router=_StubRouter(), combat=None, client=_StubClient(),
    ).start()
    # Should not raise.
    bus.publish("foundry.player_intent", {"type": "do_a_dance", "actor_id": "x"})

