"""Tests for the in-game Clock service."""
from __future__ import annotations

from ai_dm.game.clock import Clock, LONG_REST_MIN, SHORT_REST_MIN
from ai_dm.orchestration.event_bus import EventBus


def _events(bus: EventBus, name: str) -> list[dict]:
    seen: list[dict] = []
    bus.subscribe(name, lambda p: seen.append(p))
    return seen


def test_initial_state_is_day_one_morning() -> None:
    c = Clock()
    assert c.day == 1
    assert c.hh_mm == "08:00"
    assert c.phase == "day"
    assert c.total_minutes == 0


def test_advance_emits_time_advanced_with_payload() -> None:
    bus = EventBus()
    seen = _events(bus, "time.advanced")
    c = Clock(event_bus=bus)
    c.advance(45, reason="travel", scene_id="forest")
    assert len(seen) == 1
    p = seen[0]
    assert p["delta_minutes"] == 45
    assert p["reason"] == "travel"
    assert p["scene_id"] == "forest"
    assert p["hh_mm"] == "08:45"
    assert p["phase"] == "day"


def test_watch_boundaries_emit_per_crossing() -> None:
    bus = EventBus()
    watches = _events(bus, "watch.passed")
    c = Clock(event_bus=bus)            # starts at 08:00; watch boundaries every 240m
    c.advance(12 * 60, reason="march")  # 08:00 -> 20:00 crosses 12:00, 16:00, 20:00
    assert len(watches) == 3
    # Each event carries a distinct watch_index.
    assert sorted(w["watch_index"] for w in watches) == \
           [w["watch_index"] for w in watches]


def test_phase_transitions_collapse_to_one_event_per_advance() -> None:
    bus = EventBus()
    phases = _events(bus, "time.phase_changed")
    c = Clock(event_bus=bus)            # 08:00 day
    c.advance(12 * 60)                  # -> 20:00 night (skipped dusk)
    assert len(phases) == 1
    assert phases[0]["from_phase"] == "day"
    assert phases[0]["to_phase"] == "night"


def test_day_rollover_emits_event() -> None:
    bus = EventBus()
    rolls = _events(bus, "time.day_rolled")
    c = Clock(event_bus=bus)            # day 1 08:00
    c.advance(20 * 60)                  # -> day 2 04:00
    assert len(rolls) == 1
    assert rolls[0]["from_day"] == 1
    assert rolls[0]["to_day"] == 2


def test_short_and_long_rest_helpers_publish_rest_events() -> None:
    bus = EventBus()
    shorts = _events(bus, "rest.short.completed")
    longs  = _events(bus, "rest.long.completed")
    c = Clock(event_bus=bus)
    c.short_rest(scene_id="camp")
    c.long_rest(scene_id="camp")
    assert len(shorts) == 1 and shorts[0]["delta_minutes"] == SHORT_REST_MIN
    assert len(longs) == 1  and longs[0]["delta_minutes"] == LONG_REST_MIN
    assert longs[0]["scene_id"] == "camp"


def test_negative_or_zero_advance_is_a_noop() -> None:
    bus = EventBus()
    seen = _events(bus, "time.advanced")
    c = Clock(event_bus=bus)
    c.advance(0)
    c.advance(-5)
    assert seen == []
    assert c.total_minutes == 0


def test_snapshot_restore_round_trips() -> None:
    c = Clock(); c.advance(125)
    snap = c.snapshot()
    fresh = Clock(); fresh.restore(snap)
    assert fresh.total_minutes == 125
    assert fresh.hh_mm == c.hh_mm
    assert fresh.phase == c.phase


def test_restore_with_no_payload_is_safe() -> None:
    c = Clock(); c.advance(60)
    c.restore(None)
    assert c.total_minutes == 60  # unchanged


def test_publish_failure_does_not_propagate() -> None:
    class _Boom:
        def publish(self, *a, **kw): raise RuntimeError("nope")
    c = Clock(event_bus=_Boom())
    c.advance(30)  # must not raise
    assert c.total_minutes == 30

