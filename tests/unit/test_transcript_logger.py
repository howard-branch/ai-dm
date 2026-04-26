"""Tests for the per-session transcript logger."""
from __future__ import annotations

import json
from pathlib import Path

from ai_dm.app.transcript_logger import TranscriptLogger
from ai_dm.orchestration.event_bus import EventBus


def test_transcript_captures_player_input_and_narration(tmp_path: Path) -> None:
    bus = EventBus()
    logger = TranscriptLogger(
        event_bus=bus, state_root=tmp_path, session_id="testrun"
    )
    logger.start()

    bus.publish("foundry.player_input", {
        "actor_name": "Brigit", "user_name": "Alice",
        "text": "I peer into the well.",
        "scene_id": "courtyard",
    })
    bus.publish("narrator.output_ready", {
        "narration": "Cold breath rises from the dark.",
        "dialogue": [{"npc_id": "Old Beren", "text": "Mind your step.",
                      "tone": "warning"}],
        "source": "narrator",
    })

    log_text = (tmp_path / "transcripts" / "testrun.log").read_text("utf-8")
    assert "Brigit (Alice): I peer into the well." in log_text
    assert "DM: Cold breath rises from the dark." in log_text
    assert "Old Beren (warning): Mind your step." in log_text

    jsonl = (tmp_path / "transcripts" / "testrun.jsonl").read_text("utf-8")
    lines = [json.loads(l) for l in jsonl.splitlines() if l.strip()]
    kinds = [r["kind"] for r in lines]
    assert kinds == ["player_input", "narration"]
    assert lines[0]["text"] == "I peer into the well."
    assert lines[1]["narration"] == "Cold breath rises from the dark."


def test_transcript_ignores_empty_payloads(tmp_path: Path) -> None:
    bus = EventBus()
    logger = TranscriptLogger(
        event_bus=bus, state_root=tmp_path, session_id="empty"
    )
    logger.start()
    bus.publish("foundry.player_input", {"text": "  "})
    bus.publish("narrator.output_ready", {"narration": "", "dialogue": []})

    jsonl = tmp_path / "transcripts" / "empty.jsonl"
    # Only the header was written, no records appended.
    assert not jsonl.exists() or jsonl.read_text("utf-8") == ""


def test_transcript_stop_unsubscribes(tmp_path: Path) -> None:
    bus = EventBus()
    logger = TranscriptLogger(
        event_bus=bus, state_root=tmp_path, session_id="stop"
    )
    logger.start()
    logger.stop()
    bus.publish("foundry.player_input",
                {"text": "ignored", "actor_name": "X", "user_name": "Y"})
    jsonl = tmp_path / "transcripts" / "stop.jsonl"
    assert not jsonl.exists() or jsonl.read_text("utf-8") == ""

