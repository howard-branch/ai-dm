"""``RollRequestDispatcher`` must hand resolution off the recv thread.

Regression test for the apply_damage deadlock: when
``foundry.player_roll_resolved`` is published from the websocket recv
thread, ``_on_resolved`` MUST return immediately without invoking the
``roll.resolved`` subscriber chain inline (which would otherwise block
the recv loop on a downstream Foundry command response).
"""
from __future__ import annotations

import threading
import time

from ai_dm.orchestration.event_bus import EventBus
from ai_dm.orchestration.roll_request_dispatcher import RollRequestDispatcher


class _StubClient:
    def __init__(self) -> None:
        self.events: list = []

    def send_event(self, name, payload):
        self.events.append((name, payload))
        return "evt"


def _seed_pending(disp: RollRequestDispatcher) -> str:
    """Drop a fake _Pending so ``_on_resolved`` finds something to finalise."""
    from ai_dm.orchestration.roll_request_dispatcher import _Pending

    rid = "prq-test"
    disp._pending[rid] = _Pending(
        request_id=rid, actor_id="a-1", scene_id="s-1",
        roll_type="skill", key="perception", dc=15, ac=None,
        formula="1d20", advantage="normal", visibility="public",
        prompt_text="Make a Perception check (DC 15)",
        reason=None, issued_at=time.monotonic(),
        expires_at=time.monotonic() + 60.0, correlation={},
    )
    return rid


def test_on_resolved_does_not_block_caller_on_subscribers():
    bus = EventBus()
    client = _StubClient()
    disp = RollRequestDispatcher(
        event_bus=bus, client=client, timeout_s=60.0, on_timeout="cancel",
    )
    disp.start()
    try:
        # A subscriber that blocks until released. If ``_on_resolved``
        # ran subscribers inline (recv thread), ``bus.publish`` below
        # would block until ``release.set()`` — exactly the deadlock
        # the worker thread is meant to prevent.
        release = threading.Event()
        entered = threading.Event()

        def slow_subscriber(_payload):
            entered.set()
            assert release.wait(timeout=5.0), "release never fired"

        bus.subscribe("roll.resolved", slow_subscriber)

        rid = _seed_pending(disp)

        t0 = time.monotonic()
        bus.publish("foundry.player_roll_resolved", {
            "request_id": rid, "total": 17, "modifier": 3,
            "rolls": [14], "kept": [14], "success": True,
        })
        elapsed = time.monotonic() - t0
        # Recv thread must return effectively immediately.
        assert elapsed < 0.5, (
            f"_on_resolved blocked the publishing thread for {elapsed:.2f}s "
            "— subscribers are still running inline (deadlock risk)."
        )
        # The worker should have entered the slow subscriber by now.
        assert entered.wait(timeout=2.0), "worker never invoked subscriber"
        # Releasing the slow subscriber lets the worker drain.
        release.set()
        assert disp.wait_idle(timeout=2.0)
    finally:
        disp.stop()


def test_synchronous_mode_runs_inline():
    """``start(synchronous=True)`` keeps the legacy in-thread behaviour."""
    bus = EventBus()
    client = _StubClient()
    disp = RollRequestDispatcher(
        event_bus=bus, client=client, timeout_s=60.0, on_timeout="cancel",
    )
    disp.start(synchronous=True)
    try:
        seen: list = []
        bus.subscribe("roll.resolved", lambda p: seen.append(p))
        rid = _seed_pending(disp)
        bus.publish("foundry.player_roll_resolved", {
            "request_id": rid, "total": 10, "rolls": [10], "kept": [10],
        })
        # No worker — subscriber must have fired before publish returned.
        assert len(seen) == 1
    finally:
        disp.stop()

