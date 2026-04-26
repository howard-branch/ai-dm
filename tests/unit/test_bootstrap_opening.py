"""Tests for the bootstrap → opening-narration wiring.

Specifically: `_emit_opening_narration` must (a) push the chat envelope
to the Foundry client AND (b) publish a `narrator.output_ready` event
on the EventBus so the audio dispatcher reads the opening aloud.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ai_dm.app.bootstrap import _emit_opening_narration, _join_human
from ai_dm.campaign.pack import CampaignPack
from ai_dm.orchestration.event_bus import EventBus


@dataclass
class FakeClient:
    sent_events: list[tuple[str, dict[str, Any], str | None]] = field(default_factory=list)

    def send_event(self, name: str, payload: dict, *, event_id: str | None = None) -> str:
        self.sent_events.append((name, payload, event_id))
        return event_id or "evt-x"


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture()
def pack(tmp_path: Path) -> CampaignPack:
    root = tmp_path / "pack"
    state_root = tmp_path / "state"
    (root / "prompts").mkdir(parents=True)
    (root / "campaign.yaml").write_text(
        "id: testpack\nname: Test\nstart:\n  scene: courtyard\n  player_character: pc\n",
        encoding="utf-8",
    )
    locs = root / "locations" / "keep"
    _write(locs / "nodes.json", {
        "nodes": [{
            "id": "courtyard",
            "name": "Inner Courtyard",
            "description": "A cloistered limestone square at dusk.",
            "exits": {"gate": "outer_gate"},
            "features": [{"id": "object.well", "name": "Dry Well", "interactable": True}],
        }]
    })
    _write(locs / "scene_locations.json", [
        {"scene_id": "courtyard", "anchors": [], "zones": []},
    ])
    _write(locs / "npcs.json", {"npcs": []})
    return CampaignPack.load(root, state_root=state_root)


def test_join_human() -> None:
    assert _join_human([]) == ""
    assert _join_human(["a"]) == "a"
    assert _join_human(["a", "b"]) == "a and b"
    assert _join_human(["a", "b", "c"]) == "a, b and c"


def test_emit_opening_pushes_chat_event(pack: CampaignPack) -> None:
    client = FakeClient()
    bus = EventBus()
    container = SimpleNamespace(client=client, event_bus=bus)

    _emit_opening_narration(pack, container, pc_id="pc", user_id="u1")

    chat = [(n, p) for n, p, _ in client.sent_events if n == "narration"]
    assert len(chat) == 1
    payload = chat[0][1]
    assert payload["metadata"]["kind"] == "opening"
    assert payload["narration"].startswith("A cloistered limestone square")


def test_emit_opening_publishes_to_audio_bus(pack: CampaignPack) -> None:
    client = FakeClient()
    bus = EventBus()
    container = SimpleNamespace(client=client, event_bus=bus)

    received: list[dict[str, Any]] = []
    bus.subscribe("narrator.output_ready", lambda p: received.append(p))

    _emit_opening_narration(pack, container, pc_id="pc", user_id=None)

    assert len(received) == 1
    spoken = received[0]["narration"]
    # Prose first…
    assert spoken.startswith("A cloistered limestone square")
    # …then a TTS-friendly affordances sentence built from interactables.
    assert "Dry Well" in spoken
    # …then exits.
    assert "Exits:" in spoken and "gate" in spoken
    assert received[0]["source"] == "opening"


def test_emit_opening_skips_when_no_client(pack: CampaignPack) -> None:
    bus = EventBus()
    received: list[dict] = []
    bus.subscribe("narrator.output_ready", lambda p: received.append(p))
    container = SimpleNamespace(client=None, event_bus=bus)
    _emit_opening_narration(pack, container, pc_id="pc", user_id=None)
    # Without a Foundry client, we don't even attempt the audio publish
    # (the function returns early). This matches the existing contract.
    assert received == []

