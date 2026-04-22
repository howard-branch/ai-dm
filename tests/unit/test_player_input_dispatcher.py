"""Unit tests for the player_input dispatcher."""
from __future__ import annotations

from types import SimpleNamespace

from ai_dm.orchestration.actor_session import ActorSessionRegistry
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.orchestration.player_input_dispatcher import PlayerInputDispatcher


class _StubDirector:
    def __init__(self, narration: str = "you swing"):
        self.calls = []
        self._narration = narration

    def handle_player_input(self, text, *, scene_id=None, focus_npcs=None):
        self.calls.append({"text": text, "scene_id": scene_id})
        return SimpleNamespace(
            narration=self._narration,
            dialogue=[],
            metadata={"commands_ok": True},
        )


class _StubClient:
    def __init__(self):
        self.events = []

    def send_event(self, name, payload, **_):
        self.events.append((name, payload))


def _build():
    bus = EventBus()
    sessions = ActorSessionRegistry(pack=None)
    client = _StubClient()
    director = _StubDirector()
    disp = PlayerInputDispatcher(
        event_bus=bus, sessions=sessions, client=client,
        prompt_context=None, director=director,
    )
    disp.start()
    return bus, sessions, client, director, disp


def test_dispatch_runs_director_and_pushes_narration():
    bus, sessions, client, director, _ = _build()
    bus.publish("foundry.player_input", {
        "user_id": "u-1", "user_name": "Alice",
        "actor_id": "a-1", "actor_name": "Morgana",
        "scene_id": "s-1", "text": "open the door",
    })
    assert director.calls == [{"text": "open the door", "scene_id": "s-1"}]
    assert len(client.events) == 1
    name, payload = client.events[0]
    assert name == "narration"
    assert payload["narration"] == "you swing"
    assert payload["actor_id"] == "a-1"
    # Session was created and bookkeeping ran.
    assert sessions.get("a-1").turn_count == 1


def test_missing_text_is_ignored():
    bus, _, client, director, _ = _build()
    bus.publish("foundry.player_input", {
        "actor_id": "a-1", "text": "  ",
    })
    assert director.calls == []
    assert client.events == []


def test_director_failure_pushes_error_envelope():
    bus = EventBus()
    sessions = ActorSessionRegistry(pack=None)
    client = _StubClient()
    director = _StubDirector()

    def boom(*a, **kw):
        raise RuntimeError("LLM down")

    director.handle_player_input = boom
    PlayerInputDispatcher(
        event_bus=bus, sessions=sessions, client=client,
        prompt_context=None, director=director,
    ).start()
    bus.publish("foundry.player_input", {
        "actor_id": "a-1", "text": "hi",
    })
    assert len(client.events) == 1
    assert "[error" in client.events[0][1]["narration"]
    assert client.events[0][1]["commands_ok"] is False

