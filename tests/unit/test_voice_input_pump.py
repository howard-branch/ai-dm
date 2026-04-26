"""Tests for the host-side voice input pump and Foundry control bridge."""
from __future__ import annotations

import threading
import time

from ai_dm.audio.voice_input_pump import VoiceControlBridge, VoiceInputPump
from ai_dm.orchestration.event_bus import EventBus


class _FakeSpeech:
    """Minimal SpeechInput stand-in.

    ``listen_vad`` blocks on a queue of utterances. When the queue is
    empty it returns "" immediately so the pump's loop spins fast in
    tests without burning CPU.
    """

    def __init__(self, utterances: list[str]) -> None:
        self._utterances = list(utterances)
        self._lock = threading.Lock()
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def status(self) -> dict:
        return {
            "mic_tool": "fake",
            "mic_available": True,
            "transcribe_backend": "fake",
            "transcribe_available": True,
        }

    def listen_vad(self, *, cancel=None, on_speech_start=None):  # noqa: ARG002
        with self._lock:
            self.calls += 1
            if not self._utterances:
                # Sleep briefly so cancel() can interrupt cleanly.
                if cancel is not None and cancel.wait(timeout=0.05):
                    return ""
                return ""
            return self._utterances.pop(0)


class _FakeClient:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def send_event(self, name, payload=None):
        self.events.append((name, dict(payload or {})))
        return "evt-x"


def _wait_for(condition, timeout=2.0, step=0.01):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(step)
    return False


def test_voice_pump_publishes_player_input_for_each_utterance():
    bus = EventBus()
    received: list[dict] = []
    bus.subscribe("foundry.player_input", received.append)

    pump = VoiceInputPump(
        event_bus=bus,
        speech_input=_FakeSpeech(["open the door", "draw my sword"]),
        actor_id="pc_human",
        actor_name="Brigit",
        user_id="u-1",
        scene_id="scene-1",
    )

    assert pump.start() is True
    try:
        assert _wait_for(lambda: len(received) >= 2)
    finally:
        pump.stop()

    texts = [r["text"] for r in received[:2]]
    assert "open the door" in texts
    assert "draw my sword" in texts
    assert all(r["actor_id"] == "pc_human" for r in received[:2])
    assert all(r["source"] == "voice" for r in received[:2])


def test_voice_pump_stops_on_exit_phrase():
    bus = EventBus()
    received: list[dict] = []
    bus.subscribe("foundry.player_input", received.append)

    pump = VoiceInputPump(
        event_bus=bus,
        speech_input=_FakeSpeech(["hello there", "stop listening", "ignored"]),
        actor_id="pc_human",
    )
    pump.start()
    try:
        assert _wait_for(lambda: not pump.is_running, timeout=2.0)
    finally:
        pump.stop()
    # First utterance was published, "stop listening" stopped the loop,
    # the third one never fires.
    texts = [r["text"] for r in received]
    assert texts == ["hello there"]


def test_voice_pump_refuses_to_start_without_actor():
    bus = EventBus()
    pump = VoiceInputPump(
        event_bus=bus, speech_input=_FakeSpeech([]), actor_id=None,
    )
    assert pump.start() is False
    assert pump.is_running is False


def test_voice_control_bridge_toggles_pump_via_event():
    bus = EventBus()
    client = _FakeClient()
    pump = VoiceInputPump(
        event_bus=bus, speech_input=_FakeSpeech([]), client=client,
    )
    bridge = VoiceControlBridge(event_bus=bus, pump=pump, client=client)
    bridge.start()
    try:
        # /voice on with actor binding
        bus.publish("foundry.voice_control", {
            "action": "on",
            "actor_id": "pc_human",
            "user_id": "u-1",
        })
        assert _wait_for(lambda: pump.is_running)
        assert pump.actor_id == "pc_human"
        # An "on" feedback narration was sent to Foundry.
        assert any(
            n == "narration" and "[voice on]" in p.get("narration", "")
            for n, p in client.events
        )

        # /voice off
        bus.publish("foundry.voice_control", {
            "action": "off",
            "actor_id": "pc_human",
        })
        assert _wait_for(lambda: not pump.is_running)
        assert any(
            n == "narration" and "[voice off]" in p.get("narration", "")
            for n, p in client.events
        )

        # /voice toggle → on again
        bus.publish("foundry.voice_control", {
            "action": "toggle",
            "actor_id": "pc_human",
        })
        assert _wait_for(lambda: pump.is_running)
    finally:
        bridge.stop()
        pump.stop()

