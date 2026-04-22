"""Tests for the pluggable :class:`CampaignPack` abstraction."""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_dm.campaign.pack import (
    CampaignPack,
    resolve_pack,
    seed_characters,
)


def _write(p: Path, text: str = "x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _make_pack(root: Path, *, slug: str = "test", overrides: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest = f"id: {slug}\nname: Test\nversion: 0.0.1\n"
    if overrides:
        import yaml

        manifest += yaml.safe_dump({"overrides": overrides})
    (root / "campaign.yaml").write_text(manifest, encoding="utf-8")
    return root


# --------------------------------------------------------------------- #


def test_load_pack_with_default_layout(tmp_path: Path):
    pack_root = _make_pack(tmp_path / "campaigns" / "demo", slug="demo")
    _write(pack_root / "chapters" / "chapter_01" / "summary.md")
    _write(pack_root / "prompts" / "system_narrator.txt", "narrator")
    _write(pack_root / "prompts" / "output_schema.json", "{}")
    _write(pack_root / "characters" / "seed" / "hero.json", "{}")

    pack = CampaignPack.load(pack_root, state_root=tmp_path / "state")

    assert pack.slug == "demo"
    assert pack.paths.chapters == pack_root / "chapters"
    assert pack.paths.system_narrator.read_text() == "narrator"
    assert pack.paths.characters_seed == pack_root / "characters" / "seed"
    # State is keyed by slug under state_root.
    assert pack.state.root == tmp_path / "state" / "demo"
    assert pack.state.saves == tmp_path / "state" / "demo" / "saves"
    assert pack.state.backups == pack.state.saves / "backups"
    assert pack.state.characters == pack.state.root / "characters"


def test_overrides_redirect_paths(tmp_path: Path):
    pack_root = _make_pack(
        tmp_path / "p",
        slug="ov",
        overrides={"chapters": "alt_chapters", "voices": "voices/voices.json"},
    )
    pack = CampaignPack.load(pack_root, state_root=tmp_path / "state")
    assert pack.paths.chapters == pack_root / "alt_chapters"
    assert pack.paths.voices == pack_root / "voices" / "voices.json"
    # Non-overridden keys still use defaults.
    assert pack.paths.locations == pack_root / "locations"


def test_state_ensure_creates_dirs(tmp_path: Path):
    pack_root = _make_pack(tmp_path / "p", slug="e")
    pack = CampaignPack.load(pack_root, state_root=tmp_path / "state")
    pack.state.ensure()
    for d in (pack.state.characters, pack.state.saves, pack.state.backups,
              pack.state.memory, pack.state.cache):
        assert d.is_dir()


def test_seed_characters_copies_then_skips(tmp_path: Path):
    pack_root = _make_pack(tmp_path / "p", slug="s")
    _write(pack_root / "characters" / "seed" / "hero.json", '{"hp": 10}')
    _write(pack_root / "characters" / "seed" / "companions" / "bard.json", "{}")
    pack = CampaignPack.load(pack_root, state_root=tmp_path / "state")

    written = seed_characters(pack)
    assert {p.name for p in written} == {"hero.json", "bard.json"}
    live = pack.state.characters / "hero.json"
    assert live.read_text() == '{"hp": 10}'

    # Mutate live file → second seed must NOT clobber it.
    live.write_text('{"hp": 0}', encoding="utf-8")
    seed_characters(pack)
    assert live.read_text() == '{"hp": 0}'

    # Overwrite=True restores from seed.
    seed_characters(pack, overwrite=True)
    assert live.read_text() == '{"hp": 10}'


def test_resolve_pack_by_slug(tmp_path: Path):
    root = tmp_path / "campaigns"
    _make_pack(root / "demo", slug="demo")
    pack = resolve_pack("demo", campaigns_root=root, state_root=tmp_path / "state")
    assert pack.slug == "demo"


def test_resolve_pack_by_path(tmp_path: Path):
    pack_dir = _make_pack(tmp_path / "elsewhere" / "mine", slug="mine")
    pack = resolve_pack(
        str(pack_dir),
        campaigns_root=tmp_path / "campaigns",  # ignored
        state_root=tmp_path / "state",
    )
    assert pack.slug == "mine"


def test_resolve_pack_expands_user_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = Path("~/dnd/campaigns").expanduser()
    state_root = Path("~/dnd/state").expanduser()
    _make_pack(root / "demo", slug="demo")

    pack = resolve_pack("demo", campaigns_root=Path("~/dnd/campaigns"), state_root=Path("~/dnd/state"))

    assert pack.root == root / "demo"
    assert pack.state.root == state_root / "demo"


def test_resolve_pack_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        resolve_pack("nope", campaigns_root=tmp_path / "campaigns",
                     state_root=tmp_path / "state")


def test_legacy_layout_preserves_flat_saves_dir(tmp_path: Path):
    assets = tmp_path / "assets" / "campaign"
    saves = tmp_path / "data" / "saves"
    assets.mkdir(parents=True)
    saves.mkdir(parents=True)
    pack = CampaignPack.from_legacy_layout(campaign_assets=assets, saves_dir=saves)
    # In legacy mode, state.saves must be the original flat dir,
    # NOT nested under a slug.
    assert pack.state.saves == saves
    assert pack.state.backups == saves / "backups"
    # Characters seed defaults to assets/characters/ alongside campaign.
    assert pack.paths.characters_seed == tmp_path / "assets" / "characters"


def test_manifest_synthetic_when_file_missing(tmp_path: Path):
    pack_root = tmp_path / "no_manifest"
    pack_root.mkdir()
    pack = CampaignPack.load(pack_root, state_root=tmp_path / "state")
    # Manifest synthesised from directory name.
    assert pack.manifest.id == "no_manifest"

