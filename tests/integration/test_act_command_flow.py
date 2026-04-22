"""End-to-end /act flow: inbound event → director → outbound narration.

Mirrors the pattern in tests/unit/test_foundry_phase3.py
(``test_socket_bridge_republishes_inbound_events``): we feed an event
envelope into ``FoundryClient._handle_incoming`` (as if the relay had
just delivered it), and verify the dispatcher kicks off the director
and that an outbound ``send_event("narration", ...)`` is invoked.
"""
from __future__ import annotations

from types import SimpleNamespace

from ai_dm.foundry.client import FoundryClient
from ai_dm.foundry.socket_bridge import SocketBridge
from ai_dm.orchestration.actor_session import ActorSessionRegistry
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.orchestration.player_input_dispatcher import PlayerInputDispatcher
from ai_dm.orchestration.structured_intent_dispatcher import StructuredIntentDispatcher


class _RecordingClient(FoundryClient):
    """FoundryClient that records ``send_event`` calls instead of sending."""

    def __init__(self):
        super().__init__()
        self.sent_events = []

    def send_event(self, name, payload=None, **_):  # type: ignore[override]
        self.sent_events.append((name, payload))
        return "evt-test"


class _StubDirector:
    def __init__(self):
        self.calls = []

    def handle_player_input(self, text, *, scene_id=None, focus_npcs=None):
        self.calls.append({"text": text, "scene_id": scene_id})
        return SimpleNamespace(
            narration=f"You said: {text}",
            dialogue=[],
            metadata={"commands_ok": True},
        )


def test_act_command_round_trip():
    bus = EventBus()
    client = _RecordingClient()
    bridge = SocketBridge(client, bus)
    bridge.connect()  # installs on_push

    sessions = ActorSessionRegistry(pack=None)
    director = _StubDirector()
    dispatcher = PlayerInputDispatcher(
        event_bus=bus, sessions=sessions, client=client,
        prompt_context=None, director=director,
    )
    dispatcher.start()

    # Simulate the relay delivering a player_input envelope (what
    # foundry/module/scripts/chat_commands.js sends after a /act line).
    client._handle_incoming({  # noqa: SLF001 — test
        "type": "event",
        "event": "player_input",
        "event_id": "evt-1",
        "payload": {
            "user_id": "u-1", "user_name": "Alice",
            "actor_id": "a-1", "actor_name": "Morgana",
            "scene_id": "s-1", "text": "open the door",
        },
    })

    assert director.calls == [{"text": "open the door", "scene_id": "s-1"}]
    assert sessions.get("a-1").turn_count == 1
    assert client.sent_events
    name, payload = client.sent_events[0]
    assert name == "narration"
    assert payload["narration"] == "You said: open the door"
    assert payload["actor_id"] == "a-1"


def test_combat_macro_round_trip():
    """player_intent envelope → StructuredIntentDispatcher → narration."""
    import random

    from ai_dm.game.combat_machine import CombatMachine
    from ai_dm.game.combat_state import Participant

    bus = EventBus()
    client = _RecordingClient()
    bridge = SocketBridge(client, bus)
    bridge.connect()

    combat = CombatMachine(event_bus=bus, rng=random.Random(1), auto_highlight=False)
    combat.start_encounter("e1", [
        Participant(actor_id="hero", name="Hero"),
        Participant(actor_id="goblin", name="Goblin"),
    ])
    combat.roll_initiative()
    combat.begin_round()
    combat.request_action()

    StructuredIntentDispatcher(
        event_bus=bus, intent_router=None, combat=combat, client=client,
    ).start()

    client._handle_incoming({  # noqa: SLF001
        "type": "event",
        "event": "player_intent",
        "event_id": "evt-2",
        "payload": {"type": "end_turn", "actor_id": "hero"},
    })

    assert combat.state.current_index == 1
    assert client.sent_events
    name, payload = client.sent_events[-1]
    assert name == "narration"
    assert "end turn" in payload["narration"].lower()

