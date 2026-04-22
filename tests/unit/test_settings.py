"""Tests for loading campaign settings."""
from __future__ import annotations

from pathlib import Path

from ai_dm.app.settings import Settings


def test_settings_load_expands_campaign_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        """
campaigns:
  root: ~/dnd/campaigns
  active: morgana
  state_root: ~/dnd/state
""".strip(),
        encoding="utf-8",
    )

    settings = Settings.load(settings_path)

    assert settings.campaigns.root == Path("~/dnd/campaigns").expanduser()
    assert settings.campaigns.active == "morgana"
    assert settings.campaigns.state_root == Path("~/dnd/state").expanduser()


def test_settings_defaults_to_external_campaign_root_when_missing() -> None:
    settings = Settings.load(Path("/definitely/missing/settings.yaml"))

    assert settings.campaigns.root == Path("~/dnd/campaigns").expanduser()
    assert settings.campaigns.state_root == Path("data/campaigns")

