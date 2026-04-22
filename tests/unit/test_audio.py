import time

from ai_dm.audio.audio_queue import AudioItem, AudioQueue
from ai_dm.audio.narration_dispatcher import NarrationDispatcher
from ai_dm.audio.tts import (
    EdgeTTSBackend,
    FallbackTTSBackend,
    NullBackend,
    PiperBackend,
    TTSService,
)
from ai_dm.audio.voices import VoiceProfile
from ai_dm.orchestration.event_bus import EventBus


def _drain(q: AudioQueue, n: int, timeout: float = 1.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not q._processing and q.pending() == 0:  # noqa: SLF001 — test
            return
        time.sleep(0.01)


def test_null_backend_records_calls():
    nb = NullBackend()
    assert nb.synthesize("hello") == b""
    assert nb.calls == [("hello", None)]


def test_audio_queue_drains_to_sink():
    nb = NullBackend()
    received: list[tuple[str, str | None]] = []

    def sink(item, audio):
        received.append((item.text, item.tag))

    q = AudioQueue(nb, sink=sink)
    q.enqueue(AudioItem(text="alpha", priority="narration", tag="n"))
    q.enqueue(AudioItem(text="beta", priority="dialogue", tag="d"))
    _drain(q, 2)
    q.shutdown()
    # Narration is higher priority than dialogue and was enqueued first → first.
    assert ("alpha", "n") in received
    assert ("beta", "d") in received


def test_priority_orders_system_first():
    nb = NullBackend()
    received: list[str] = []

    def sink(item, audio):
        received.append(item.text)

    # Don't autostart; queue everything first then start so priority decides order.
    q = AudioQueue(nb, sink=sink, autostart=False)
    q.enqueue(AudioItem(text="d", priority="dialogue"))
    q.enqueue(AudioItem(text="n", priority="narration"))
    q.enqueue(AudioItem(text="s", priority="system"))
    q.start()
    _drain(q, 3)
    q.shutdown()
    assert received[0] == "s"
    assert received[1] == "n"


def test_interrupt_clears_pending():
    nb = NullBackend()
    q = AudioQueue(nb, autostart=False)
    for i in range(5):
        q.enqueue(AudioItem(text=f"x{i}"))
    q.interrupt()
    assert q.pending() == 0


def test_tts_service_falls_back_silently():
    # Both backends unavailable → empty bytes, no crash.
    svc = TTSService(FallbackTTSBackend(NullBackend(), NullBackend()))
    assert svc.speak("hi") == b""


def test_edge_tts_optional_import():
    # Only check ``is_available`` doesn't crash; package may or may not be installed.
    backend = EdgeTTSBackend()
    assert isinstance(backend.is_available(), bool)


def test_piper_unavailable_when_binary_missing():
    backend = PiperBackend(binary="this-binary-does-not-exist-xyz")
    assert backend.is_available() is False


def test_voices_default_and_per_npc():
    vp = VoiceProfile(
        default_voice="en-GB-SoniaNeural",
        npc_voices={"morgana": "en-GB-LibbyNeural"},
    )
    assert vp.for_npc(None) == "en-GB-SoniaNeural"
    assert vp.for_npc("unknown") == "en-GB-SoniaNeural"
    assert vp.for_npc("morgana") == "en-GB-LibbyNeural"


def test_narration_dispatcher_routes_voices():
    bus = EventBus()
    nb = NullBackend()
    received: list[tuple[str, str | None, str | None]] = []

    def sink(item, audio):
        received.append((item.text, item.voice, item.speaker_id))

    q = AudioQueue(nb, sink=sink)
    voices = VoiceProfile(default_voice="V-default", npc_voices={"morgana": "V-morgana"})
    disp = NarrationDispatcher(event_bus=bus, queue=q, voices=voices)
    disp.start()

    bus.publish(
        "narrator.output_ready",
        {
            "narration": "A wind moves through the corridor.",
            "spoken_dialogue": "",
            "dialogue": [
                {"npc_id": "morgana", "text": "Tread carefully."},
                {"npc_id": "captain", "text": "Halt!"},
            ],
        },
    )
    _drain(q, 3)
    q.shutdown()
    voices_used = {(text, voice, sid) for text, voice, sid in received}
    assert any(v == "V-morgana" and sid == "morgana" for _t, v, sid in received)
    assert any(v == "V-default" for _t, v, _s in received)
    assert any(t.startswith("A wind") for t, _v, _s in received)

