"""Tests for the shared catalog overlay loader."""
from __future__ import annotations

import json
from pathlib import Path

from ai_dm.app.catalog_loader import deep_merge, load_overlay
from ai_dm.app.character_equipment import (
    apply_kit,
    load_items_catalog,
    load_starting_kits,
)
from ai_dm.app.character_features import features_for, load_class_features
from ai_dm.app.character_spells import available_spells, load_spell_catalog
from ai_dm.campaign.pack import (
    CampaignManifest,
    CampaignPack,
    CampaignPaths,
    CampaignState,
)


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #


def _make_pack(tmp_path: Path) -> CampaignPack:
    root = tmp_path / "pack"
    state_root = tmp_path / "state"
    (root / "rules").mkdir(parents=True)
    (root / "characters" / "seed").mkdir(parents=True)
    state_root.mkdir(parents=True)
    manifest = CampaignManifest(id="testpack", name="Test Pack")
    paths = CampaignPaths(root=root)
    state = CampaignState(root=state_root / manifest.id)
    state.ensure()
    return CampaignPack(root=root, manifest=manifest, paths=paths, state=state)


def _write_overlay(pack: CampaignPack, filename: str, data: dict) -> None:
    (pack.paths.rules / filename).write_text(json.dumps(data), encoding="utf-8")


# --------------------------------------------------------------------- #
# deep_merge
# --------------------------------------------------------------------- #


def test_deep_merge_overlay_wins_on_scalars() -> None:
    out = deep_merge({"a": 1, "b": 2}, {"b": 3, "c": 4})
    assert out == {"a": 1, "b": 3, "c": 4}


def test_deep_merge_recurses_into_dicts() -> None:
    base = {"sword": {"name": "Sword", "value_gp": 10, "weight": 3}}
    overlay = {"sword": {"value_gp": 99, "damage": "2d6"}}
    out = deep_merge(base, overlay)
    assert out == {"sword": {"name": "Sword", "value_gp": 99, "weight": 3, "damage": "2d6"}}


def test_deep_merge_replaces_lists_wholesale() -> None:
    base = {"k": {"items": [1, 2, 3]}}
    overlay = {"k": {"items": [9]}}
    out = deep_merge(base, overlay)
    assert out == {"k": {"items": [9]}}


def test_deep_merge_does_not_mutate_inputs() -> None:
    base = {"a": {"x": 1}}
    overlay = {"a": {"y": 2}}
    deep_merge(base, overlay)
    assert base == {"a": {"x": 1}}
    assert overlay == {"a": {"y": 2}}


# --------------------------------------------------------------------- #
# load_overlay
# --------------------------------------------------------------------- #


def test_load_overlay_base_only_when_no_pack(tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "cat.json").write_text(json.dumps({"a": 1, "_doc": "ignored"}))
    out = load_overlay("cat.json", pack=None, base_dir=base_dir)
    assert out == {"a": 1}


def test_load_overlay_strips_doc_keys(tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "cat.json").write_text(json.dumps({"a": 1, "_meta": "x"}))
    assert load_overlay("cat.json", base_dir=base_dir) == {"a": 1}


def test_load_overlay_missing_base_and_overlay_returns_empty(tmp_path: Path) -> None:
    pack = _make_pack(tmp_path)
    assert load_overlay("nope.json", pack=pack, base_dir=tmp_path / "no_base") == {}


def test_load_overlay_overlay_layered_on_base(tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "items.json").write_text(json.dumps({
        "sword": {"name": "Sword", "value_gp": 10},
        "shield": {"name": "Shield", "value_gp": 5},
    }))
    pack = _make_pack(tmp_path)
    _write_overlay(pack, "items.json", {
        "sword": {"value_gp": 99},                 # nested override
        "necronomicon": {"name": "Necronomicon"},  # new themed item
    })
    out = load_overlay("items.json", pack=pack, base_dir=base_dir)
    assert out == {
        "sword": {"name": "Sword", "value_gp": 99},
        "shield": {"name": "Shield", "value_gp": 5},
        "necronomicon": {"name": "Necronomicon"},
    }


def test_load_overlay_missing_overlay_falls_back_to_base(tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "items.json").write_text(json.dumps({"sword": {"name": "Sword"}}))
    pack = _make_pack(tmp_path)  # no overlay file written
    assert load_overlay("items.json", pack=pack, base_dir=base_dir) == {
        "sword": {"name": "Sword"}
    }


# --------------------------------------------------------------------- #
# Pack-aware loaders end-to-end
# --------------------------------------------------------------------- #


def test_load_items_catalog_with_pack_overlay_adds_themed_item(tmp_path: Path) -> None:
    pack = _make_pack(tmp_path)
    _write_overlay(pack, "dnd5e_items.json", {
        "cursed_blade": {"name": "Cursed Blade", "type": "weapon", "value_gp": 0,
                         "damage": "1d8 necrotic", "weapon_type": "melee"},
    })
    cat = load_items_catalog(pack=pack)
    assert "cursed_blade" in cat
    assert cat["cursed_blade"]["name"] == "Cursed Blade"
    # Shared catalog entries must still be present.
    assert any(k != "cursed_blade" for k in cat)


def test_load_starting_kits_with_pack_overlay_overrides_archetype_kit(tmp_path: Path) -> None:
    pack = _make_pack(tmp_path)
    base_kits = load_starting_kits()  # shared
    assert "warrior" in base_kits
    _write_overlay(pack, "dnd5e_starting_kits.json", {
        "warrior": {
            "items": [{"id": "longsword", "qty": 1, "equipped": True}],
            "currency": {"gp": 12},
            "shopping_budget_gp": 0,
        },
    })
    overlaid = load_starting_kits(pack=pack)
    assert overlaid["warrior"]["currency"] == {"gp": 12}
    assert overlaid["warrior"]["shopping_budget_gp"] == 0
    # Other archetypes preserved from shared catalog.
    assert "rogue" in overlaid


def test_apply_kit_uses_pack_overlay(tmp_path: Path) -> None:
    pack = _make_pack(tmp_path)
    _write_overlay(pack, "dnd5e_items.json", {
        "skull_orb": {"name": "Skull Orb", "type": "focus", "value_gp": 0},
    })
    _write_overlay(pack, "dnd5e_starting_kits.json", {
        "witch": {
            "items": [{"id": "skull_orb", "qty": 1, "equipped": True}],
            "currency": {"gp": 5},
            "shopping_budget_gp": 0,
        },
    })
    inv, currency, budget = apply_kit("witch", pack=pack)
    ids = [it["id"] for it in inv]
    assert "skull_orb" in ids
    assert currency["gp"] == 5
    assert budget == 0


def test_load_spell_catalog_with_overlay_adds_themed_spell(tmp_path: Path) -> None:
    pack = _make_pack(tmp_path)
    _write_overlay(pack, "dnd5e_spells.json", {
        "death_whisper": {
            "name": "Death Whisper", "level": 0, "school": "necromancy",
            "casting_time": "action", "range": "30 ft",
            "components": {"v": True}, "duration": "instantaneous",
            "archetypes": ["witch"],
        },
    })
    pool = available_spells("witch", 0, pack=pack)
    assert any(sid == "death_whisper" for sid, _ in pool)


def test_load_class_features_with_overlay_extends_archetype_features(tmp_path: Path) -> None:
    pack = _make_pack(tmp_path)
    base = load_class_features()
    _write_overlay(pack, "dnd5e_class_features.json", {
        "witch": [
            {"name": "Soul Mark", "summary": "Bind a soul to a phylactery."},
        ],
    })
    feats = features_for("witch", None, pack=pack)
    names = [f["name"] for f in feats]
    assert "Soul Mark" in names
    # When the shared catalog also has witch features, the overlay's
    # list replaces them wholesale (per documented merge semantics).
    base_witch = [f["name"] for f in (base.get("witch") or []) if isinstance(f, dict)]
    for n in base_witch:
        assert n not in names, "list should be replaced wholesale by overlay"


def test_loaders_without_pack_match_shared_catalog() -> None:
    # Sanity: passing pack=None (the default) is equivalent to loading
    # the shared catalog as-is. Guards against regressions.
    assert load_items_catalog() == load_items_catalog(pack=None)
    assert load_starting_kits() == load_starting_kits(pack=None)
    assert load_spell_catalog() == load_spell_catalog(pack=None)
    assert load_class_features() == load_class_features(pack=None)

