"""End-to-end (in-process) test of the player-roll request lifecycle.

Exercises:
    1. ``DiceRequest`` from the AI narrator → ``RollRequestDispatcher``
       emits ``request_player_roll`` event via the FoundryClient stub.
    2. Synthetic ``foundry.player_roll_resolved`` arrives → record is
       written to the RollLog, ``roll.resolved`` is published, and a
       follow-up ``foundry.player_input`` is enqueued so the narration
       loop resumes.
    3. Auto-roll on timeout uses the DMRoller and still produces a
       resolved record + follow-up turn.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ai_dm.ai.schemas import DiceRequest
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.orchestration.roll_request_dispatcher import RollRequestDispatcher
from ai_dm.persistence.roll_log import RollLog
from ai_dm.rules.dm_rolls import DMRoller


class _StubClient:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def send_event(self, name: str, payload: dict[str, Any]) -> str:
        self.events.append((name, dict(payload)))
        return f"evt-{len(self.events)}"


def test_player_roll_round_trip(tmp_path: Path):
    bus = EventBus()
    client = _StubClient()
    log = RollLog(state_root=tmp_path)
    dm = DMRoller(roll_log=log, client=client, event_bus=bus)
    disp = RollRequestDispatcher(
        event_bus=bus, client=client, roll_log=log, dm_roller=dm,
        timeout_s=60.0, on_timeout="auto_roll",
    )
    disp.start()
    try:
        # Capture downstream events for assertions.
        resolved: list[dict] = []
        followups: list[dict] = []
        bus.subscribe("roll.resolved", lambda p: resolved.append(p))
        bus.subscribe("foundry.player_input", lambda p: followups.append(p))

        rid = disp.emit_from_dice_request(
            DiceRequest(type="skill", skill="perception", dc=15, reason="scan"),
            actor_id="Sansa",
            scene_id="chapel",
        )
        assert rid is not None
        # Outbound event was dispatched.
        assert any(name == "request_player_roll" for name, _ in client.events)
        out = next(p for n, p in client.events if n == "request_player_roll")
        assert out["request_id"] == rid
        assert out["dc"] == 15
        assert out["roll_type"] == "skill"
        assert out["key"] == "perception"
        assert out["prompt_text"]

        # Foundry "responds" via the inbound bridge.
        bus.publish("foundry.player_roll_resolved", {
            "request_id": rid,
            "actor_id": "Sansa",
            "formula": "1d20+3",
            "total": 17,
            "modifier": 3,
            "rolls": [14],
            "kept": [14],
            "success": True,
            "crit": False,
            "fumble": False,
        })
        # Resolution runs on the dispatcher's worker thread now (so
        # the websocket recv loop never deadlocks behind a downstream
        # ``apply_damage``). Drain it before asserting.
        assert disp.wait_idle(timeout=2.0)
        # Audit log has the player record.
        records = list(log.iter_records())
        assert any(r["source"] == "player" and r["total"] == 17 for r in records)
        # roll.resolved was published with the record.
        assert resolved and resolved[0]["record"]["total"] == 17
        # Follow-up turn was enqueued for the planner.
        assert followups
        assert followups[0]["actor_id"] == "Sansa"
        assert "perception" in followups[0]["text"]
        assert "17" in followups[0]["text"]
    finally:
        disp.stop()


def test_auto_roll_on_timeout(tmp_path: Path):
    bus = EventBus()
    client = _StubClient()
    log = RollLog(state_root=tmp_path)
    dm = DMRoller(roll_log=log, client=client, event_bus=bus)
    disp = RollRequestDispatcher(
        event_bus=bus, client=client, roll_log=log, dm_roller=dm,
        timeout_s=0.5, on_timeout="auto_roll",
    )
    disp.start()
    try:
        followups: list[dict] = []
        bus.subscribe("foundry.player_input", lambda p: followups.append(p))
        rid = disp.emit_from_dice_request(
            DiceRequest(type="save", skill="dex", dc=12),
            actor_id="Vex",
            scene_id="cellar",
        )
        assert rid
        # Wait past the timeout for the reaper to fire.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not followups:
            time.sleep(0.1)
        assert followups, "expected auto-roll follow-up turn"
        assert followups[0]["actor_id"] == "Vex"
        # The DMRoller wrote at least one record.
        records = list(log.iter_records())
        assert any(r["actor_id"] == "Vex" for r in records)
    finally:
        disp.stop()

