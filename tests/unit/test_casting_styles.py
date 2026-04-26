"""Tests for the known-vs-prepared casting style split (Phase 3)."""
from __future__ import annotations

import json
from pathlib import Path

from ai_dm.app.bootstrap import _migrate_spell_block
from ai_dm.app.character_spells import pick_starting_spells, prepare_spells
from ai_dm.app.character_wizard import ARCHETYPES, _spellcasting_for, build_sheet


# --------------------------------------------------------------------- #
# pick_starting_spells: shape per casting style
# --------------------------------------------------------------------- #


def _spellcasting_witch() -> dict:
    return _spellcasting_for(ARCHETYPES["witch"], level=1) or {}


def _spellcasting_scholar() -> dict:
    return _spellcasting_for(ARCHETYPES["scholar"], level=1) or {}


def test_pick_known_emits_known_list_and_empty_prepared() -> None:
    block, errs = pick_starting_spells(
        "witch",
        cantrip_picks=["eldritch_blast"],
        spell_picks=["hex"],
        spellcasting=_spellcasting_witch(),
        casting_style="known",
    )
    assert errs == []
    assert block["casting_style"] == "known"
    assert [s["id"] for s in block["known"]] == ["hex"]
    assert block["prepared"] == []
    assert "spellbook" not in block
    # cantrips are always castable
    assert all(s["prepared"] is True for s in block["cantrips_known"])
    # known spells marked prepared (always castable for the "known" family)
    assert all(s["prepared"] is True for s in block["known"])


def test_pick_prepared_emits_spellbook_and_prepared_subset() -> None:
    block, errs = pick_starting_spells(
        "scholar",
        cantrip_picks=["light"],
        spell_picks=["magic_missile", "shield"],
        spellcasting=_spellcasting_scholar(),
        casting_style="prepared",
    )
    assert errs == []
    assert block["casting_style"] == "prepared"
    book_ids = sorted(s["id"] for s in block["spellbook"])
    prep_ids = sorted(s["id"] for s in block["prepared"])
    assert book_ids == ["magic_missile", "shield"]
    # At level 1 prepared defaults to the full spellbook.
    assert prep_ids == book_ids
    assert block["known"] == []


def test_pick_legacy_shape_when_casting_style_omitted() -> None:
    block, errs = pick_starting_spells(
        "witch",
        cantrip_picks=[],
        spell_picks=["hex"],
        spellcasting=_spellcasting_witch(),
    )
    assert errs == []
    # Legacy v1 shape: known == prepared, no casting_style marker.
    assert "casting_style" not in block
    assert [s["id"] for s in block["known"]] == ["hex"]
    assert [s["id"] for s in block["prepared"]] == ["hex"]


def test_pick_non_caster_returns_empty_block() -> None:
    block, errs = pick_starting_spells(
        "rogue",
        cantrip_picks=None,
        spell_picks=None,
        spellcasting=None,
        casting_style=None,
    )
    assert errs == []
    assert block == {"cantrips_known": [], "prepared": [], "known": [], "slots": {}}


# --------------------------------------------------------------------- #
# prepare_spells
# --------------------------------------------------------------------- #


def test_prepare_spells_narrows_prepared_to_subset_of_spellbook() -> None:
    block, _ = pick_starting_spells(
        "scholar",
        cantrip_picks=[],
        spell_picks=["magic_missile", "shield"],
        spellcasting=_spellcasting_scholar(),
        casting_style="prepared",
    )
    new_block, errs = prepare_spells(block, picks=["shield"], cap=1)
    assert errs == []
    assert [s["id"] for s in new_block["prepared"]] == ["shield"]
    # Spellbook flags reflect the new prepared subset.
    by_id = {s["id"]: s["prepared"] for s in new_block["spellbook"]}
    assert by_id == {"magic_missile": False, "shield": True}
    # Original block unchanged (immutability).
    assert all(s["prepared"] for s in block["prepared"])


def test_prepare_spells_drops_unknown_ids_with_error() -> None:
    block, _ = pick_starting_spells(
        "scholar", [], ["magic_missile"],
        spellcasting=_spellcasting_scholar(),
        casting_style="prepared",
    )
    _, errs = prepare_spells(block, picks=["fireball"])
    assert any("not in the spellbook" in e for e in errs)


def test_prepare_spells_enforces_cap() -> None:
    block, _ = pick_starting_spells(
        "scholar", [], ["magic_missile", "shield"],
        spellcasting=_spellcasting_scholar(),
        casting_style="prepared",
    )
    new_block, errs = prepare_spells(
        block, picks=["magic_missile", "shield"], cap=1,
    )
    assert len(new_block["prepared"]) == 1
    assert any("cap is 1" in e for e in errs)


def test_prepare_spells_rejects_known_caster() -> None:
    block, _ = pick_starting_spells(
        "witch", [], ["hex"],
        spellcasting=_spellcasting_witch(),
        casting_style="known",
    )
    same, errs = prepare_spells(block, picks=["hex"])
    assert same is block
    assert errs == ["cannot prepare spells for a 'known' caster"]


def test_prepare_spells_dedupes_and_strips_blanks() -> None:
    block, _ = pick_starting_spells(
        "scholar", [], ["magic_missile", "shield"],
        spellcasting=_spellcasting_scholar(),
        casting_style="prepared",
    )
    new_block, errs = prepare_spells(
        block, picks=["shield", "  ", "shield"],
    )
    assert [s["id"] for s in new_block["prepared"]] == ["shield"]
    assert errs == []


# --------------------------------------------------------------------- #
# build_sheet integration: archetype's casting_style flows through
# --------------------------------------------------------------------- #


def test_build_sheet_witch_uses_known_shape() -> None:
    sheet = build_sheet(
        "m", "Morgana", "witch", None, "forbidden_scholar",
        spell_picks=["hex"],
    )
    assert sheet["spells"]["casting_style"] == "known"
    assert [s["id"] for s in sheet["spells"]["known"]] == ["hex"]
    assert sheet["spells"]["prepared"] == []


def test_build_sheet_scholar_uses_prepared_shape() -> None:
    sheet = build_sheet(
        "s", "Sage", "scholar", None, "forbidden_scholar",
        spell_picks=["magic_missile"],
    )
    assert sheet["spells"]["casting_style"] == "prepared"
    assert [s["id"] for s in sheet["spells"]["spellbook"]] == ["magic_missile"]
    assert [s["id"] for s in sheet["spells"]["prepared"]] == ["magic_missile"]


# --------------------------------------------------------------------- #
# Migration shim
# --------------------------------------------------------------------- #


def _legacy_witch_sheet() -> dict:
    return {
        "id": "m", "name": "M", "class": "Witch",
        "spells": {
            "cantrips_known": [{"id": "chill_touch", "name": "Chill Touch", "level": 0, "prepared": True}],
            "prepared": [{"id": "hex", "name": "Hex", "level": 1, "prepared": True}],
            "known":    [{"id": "hex", "name": "Hex", "level": 1, "prepared": True}],
            "slots": {"1": {"max": 1, "value": 1}},
        },
    }


def _legacy_scholar_sheet() -> dict:
    return {
        "id": "s", "name": "S", "class": "Scholar",
        "spells": {
            "cantrips_known": [],
            "prepared": [{"id": "magic_missile", "name": "Magic Missile", "level": 1, "prepared": True}],
            "known":    [{"id": "magic_missile", "name": "Magic Missile", "level": 1, "prepared": True}],
            "slots": {"1": {"max": 2, "value": 2}},
        },
    }


def test_migrate_legacy_witch_to_known_clears_prepared() -> None:
    out = _migrate_spell_block(_legacy_witch_sheet())
    assert out["spells"]["casting_style"] == "known"
    assert [s["id"] for s in out["spells"]["known"]] == ["hex"]
    assert out["spells"]["prepared"] == []


def test_migrate_legacy_scholar_to_prepared_copies_to_spellbook() -> None:
    out = _migrate_spell_block(_legacy_scholar_sheet())
    assert out["spells"]["casting_style"] == "prepared"
    assert [s["id"] for s in out["spells"]["spellbook"]] == ["magic_missile"]
    assert [s["id"] for s in out["spells"]["prepared"]] == ["magic_missile"]
    assert out["spells"]["known"] == []


def test_migrate_idempotent_on_already_migrated_sheet() -> None:
    once = _migrate_spell_block(_legacy_scholar_sheet())
    twice = _migrate_spell_block(once)
    assert twice == once  # casting_style marker prevents re-migration


def test_migrate_no_op_for_non_caster_sheet() -> None:
    sheet = {"id": "r", "class": "Rogue", "spells": {"cantrips_known": [], "prepared": [], "known": [], "slots": {}}}
    assert _migrate_spell_block(sheet) == sheet


def test_migrate_no_op_for_unknown_class() -> None:
    sheet = {"id": "x", "class": "Bard", "spells": {"prepared": [], "known": []}}
    assert _migrate_spell_block(sheet) == sheet


def test_load_character_sheet_runs_migration(tmp_path: Path) -> None:
    """End-to-end: writing a legacy sheet and loading it through bootstrap
    yields a migrated v2 sheet."""
    from ai_dm.app.bootstrap import _load_character_sheet
    from ai_dm.campaign.pack import (
        CampaignManifest, CampaignPack, CampaignPaths, CampaignState,
    )
    root = tmp_path / "pack"
    state_root = tmp_path / "state"
    (root / "characters" / "seed").mkdir(parents=True)
    state_root.mkdir(parents=True)
    pack = CampaignPack(
        root=root,
        manifest=CampaignManifest(id="t", name="t"),
        paths=CampaignPaths(root=root),
        state=CampaignState(root=state_root / "t"),
    )
    pack.state.ensure()
    (pack.state.characters / "s.json").write_text(json.dumps(_legacy_scholar_sheet()))
    loaded = _load_character_sheet(pack, "s")
    assert loaded is not None
    assert loaded["spells"]["casting_style"] == "prepared"
    assert "spellbook" in loaded["spells"]

