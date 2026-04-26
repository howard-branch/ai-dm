"""Tests for the lobby gate."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from ai_dm.app.lobby import LobbyConfig, _summarise, wait_for_lobby_ready
from ai_dm.campaign.pack import CampaignManifest, CampaignPack, CampaignPaths, CampaignState
from ai_dm.orchestration.event_bus import EventBus


# --------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------- #


@dataclass
class FakeClient:
    censuses: list[dict[str, Any]] = field(default_factory=list)
    sent_events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    _idx: int = 0

    def who(self, *, timeout: float = 1.0) -> dict[str, Any] | None:
        if not self.censuses:
            return None
        idx = min(self._idx, len(self.censuses) - 1)
        self._idx += 1
        return self.censuses[idx]

    def send_event(self, name: str, payload: dict, *, event_id: str | None = None) -> str:
        self.sent_events.append((name, payload))
        return event_id or "evt-x"


def _pack(tmp_path, expected_players: list[str] | None = None) -> CampaignPack:
    start = {"scene": "courtyard", "player_character": "pc"}
    if expected_players is not None:
        start["expected_players"] = expected_players
    manifest = CampaignManifest(id="testpack", name="Test Pack", start=start)
    return CampaignPack(
        root=tmp_path,
        manifest=manifest,
        paths=CampaignPaths(root=tmp_path),
        state=CampaignState(root=tmp_path / "state"),
    )


def _container(client: FakeClient, bus: EventBus) -> SimpleNamespace:
    return SimpleNamespace(client=client, event_bus=bus)


# --------------------------------------------------------------------- #
# Pure summary logic
# --------------------------------------------------------------------- #


def test_summarise_marks_ready_when_gm_and_all_expected_present() -> None:
    census = {
        "foundry_gm_count": 1,
        "foundry_clients": [
            {"user_name": "Gamemaster", "is_gm": True},
            {"user_name": "alice", "is_gm": False},
        ],
    }
    s = _summarise(census, ["alice"])
    assert s["gm_present"] is True
    assert "alice" in s["connected"]
    assert s["missing"] == []
    assert s["ready"] is True


def test_summarise_lists_missing_players() -> None:
    census = {
        "foundry_gm_count": 1,
        "foundry_clients": [{"user_name": "GM", "is_gm": True}],
    }
    s = _summarise(census, ["alice", "bob"])
    assert s["missing"] == ["alice", "bob"]
    assert s["ready"] is False


def test_summarise_handles_no_census() -> None:
    s = _summarise(None, ["alice"])
    assert s == {
        "gm_present": False,
        "connected": [],
        "missing": ["alice"],
        "expected": ["alice"],
        "ready": False,
    }


# --------------------------------------------------------------------- #
# Gate behaviour
# --------------------------------------------------------------------- #


def test_autostart_env_skips_gate(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AI_DM_AUTOSTART", "1")
    bus = EventBus()
    client = FakeClient()
    pack = _pack(tmp_path)
    assert wait_for_lobby_ready(pack, _container(client, bus)) is True
    # Should never have polled the relay.
    assert client.sent_events == []


def test_returns_false_when_no_client(tmp_path) -> None:
    pack = _pack(tmp_path)
    container = SimpleNamespace(client=None, event_bus=EventBus())
    assert wait_for_lobby_ready(pack, container) is False


def test_start_game_event_unblocks_immediately(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AI_DM_AUTOSTART", raising=False)
    bus = EventBus()
    client = FakeClient(censuses=[{
        "foundry_gm_count": 1,
        "foundry_clients": [{"user_name": "GM", "is_gm": True}],
    }])
    pack = _pack(tmp_path, expected_players=["alice"])  # alice never arrives
    container = _container(client, bus)
    cfg = LobbyConfig(poll_interval=0.05, timeout=5.0, auto_grace=0.5)

    result: dict[str, Any] = {}

    def _runner() -> None:
        result["ok"] = wait_for_lobby_ready(pack, container, config=cfg)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    # Give the gate one poll cycle so it has subscribed and emitted status.
    time.sleep(0.15)
    bus.publish("foundry.start_game", {"user_name": "GM"})
    t.join(timeout=2.0)
    assert result.get("ok") is True
    # At least one lobby_status whisper was pushed.
    assert any(name == "lobby_status" for name, _ in client.sent_events)


def test_auto_start_when_all_expected_present(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AI_DM_AUTOSTART", raising=False)
    bus = EventBus()
    client = FakeClient(censuses=[{
        "foundry_gm_count": 1,
        "foundry_clients": [
            {"user_name": "GM", "is_gm": True},
            {"user_name": "alice", "is_gm": False},
        ],
    }])
    pack = _pack(tmp_path, expected_players=["alice"])
    cfg = LobbyConfig(poll_interval=0.05, timeout=5.0, auto_grace=0.1)
    assert wait_for_lobby_ready(pack, _container(client, bus), config=cfg) is True


def test_status_event_only_published_when_picture_changes(
    tmp_path, monkeypatch,
) -> None:
    """If the census doesn't change, we don't spam GM chat with duplicates."""
    monkeypatch.delenv("AI_DM_AUTOSTART", raising=False)
    bus = EventBus()
    same_census = {
        "foundry_gm_count": 1,
        "foundry_clients": [{"user_name": "GM", "is_gm": True}],
    }
    client = FakeClient(censuses=[same_census, same_census, same_census])
    pack = _pack(tmp_path, expected_players=["alice"])
    container = _container(client, bus)
    cfg = LobbyConfig(poll_interval=0.05, timeout=0.5, auto_grace=10.0)

    # No /startgame, no alice → returns False on timeout.
    assert wait_for_lobby_ready(pack, container, config=cfg) is False
    # Census never changed, so exactly one lobby_status was emitted.
    pushes = [name for name, _ in client.sent_events if name == "lobby_status"]
    assert len(pushes) == 1

