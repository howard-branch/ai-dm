"""Tests for the guided character-creation wizard."""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import pytest

from ai_dm.app.character_wizard import (
    ARCHETYPES,
    BACKGROUNDS,
    build_sheet,
    needs_wizard,
    run_wizard,
    validate_response,
    sheet_path,
    write_sheet,
)
from ai_dm.campaign.pack import (
    CampaignManifest,
    CampaignPack,
    CampaignPaths,
    CampaignState,
)


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #


def _make_pack(tmp_path: Path, *, pc_id: str = "hero") -> CampaignPack:
    root = tmp_path / "pack"
    state_root = tmp_path / "state"
    (root / "characters" / "seed").mkdir(parents=True)
    state_root.mkdir(parents=True)
    manifest = CampaignManifest(
        id="testpack",
        name="Test Pack",
        start={"player_character": pc_id, "scene": "start"},
    )
    paths = CampaignPaths(root=root)
    state = CampaignState(root=state_root / manifest.id)
    state.ensure()
    return CampaignPack(root=root, manifest=manifest, paths=paths, state=state)


def _scripted(*lines: str):
    q = deque(lines)
    sink: list[str] = []

    def input_fn(_prompt: str) -> str:
        if not q:
            raise EOFError("scripted input exhausted")
        return q.popleft()

    def print_fn(msg: str) -> None:
        sink.append(msg)

    return input_fn, print_fn, sink


# --------------------------------------------------------------------- #
# build_sheet
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("key", list(ARCHETYPES))
def test_build_sheet_each_archetype_has_required_fields(key: str) -> None:
    sheet = build_sheet("hero", "Hero", key, None, "exiled_noble")
    for required in (
        "id", "name", "class", "level", "background",
        "abilities", "ac", "hp", "speed", "features",
        "inventory", "conditions",
    ):
        assert required in sheet, f"missing {required} for archetype {key}"
    assert sheet["abilities"] == ARCHETYPES[key].abilities
    assert sheet["class"] == ARCHETYPES[key].label
    assert sheet["hp"]["max"] == ARCHETYPES[key].hp


def test_build_sheet_witch_defaults() -> None:
    sheet = build_sheet("morgana", "Morgana", "witch", None, "forbidden_scholar")
    assert sheet["id"] == "morgana"
    assert sheet["name"] == "Morgana"
    assert sheet["class"] == "Witch"
    assert sheet["background"] == "Forbidden Scholar"
    assert sheet["abilities"]["cha"] == 16
    assert sheet["ac"] == 12
    # personality + notes seeded from background
    assert sheet["personality"] == BACKGROUNDS["forbidden_scholar"].personality
    assert sheet["notes"] == BACKGROUNDS["forbidden_scholar"].blurb


def test_stat_bump_applied_once_to_named_stat() -> None:
    base = ARCHETYPES["witch"].abilities
    sheet = build_sheet("m", "M", "witch", "int", "exiled_noble")
    for k, v in base.items():
        if k == "int":
            assert sheet["abilities"][k] == v + 1
        else:
            assert sheet["abilities"][k] == v


def test_stat_bump_unknown_value_is_ignored() -> None:
    sheet = build_sheet("m", "M", "witch", "luck", "exiled_noble")
    assert sheet["abilities"] == ARCHETYPES["witch"].abilities


# --------------------------------------------------------------------- #
# run_wizard
# --------------------------------------------------------------------- #


def test_run_wizard_with_scripted_input() -> None:
    # name=Morgana, archetype=1 (witch), bump=4 (int), background=2 (forbidden_scholar),
    # skip shopping, skip cantrips, skip spells, confirm=y
    input_fn, print_fn, _ = _scripted(
        "Morgana",
        "1",       # archetype: first option (witch)
        "4",       # +1 to INT (4th in str/dex/con/int/wis/cha)
        "2",       # background: second option (forbidden_scholar)
        "n",       # skip shopping
        "",        # skip cantrip picks (witch is a caster)
        "",        # skip leveled spell picks
        "y",       # confirm
    )
    sheet = run_wizard("morgana", input_fn=input_fn, print_fn=print_fn)
    assert sheet["name"] == "Morgana"
    assert sheet["id"] == "morgana"
    assert sheet["class"] == "Witch"
    assert sheet["background"] == "Forbidden Scholar"
    assert sheet["abilities"]["int"] == ARCHETYPES["witch"].abilities["int"] + 1


def test_run_wizard_accepts_keys_instead_of_indices() -> None:
    input_fn, print_fn, _ = _scripted(
        "Aria",
        "rogue",
        "skip",
        "wandering_mage",
        "n",       # skip shopping (rogue has a budget)
        "",        # default yes on confirm
    )
    sheet = run_wizard(input_fn=input_fn, print_fn=print_fn)
    assert sheet["class"] == "Rogue"
    assert sheet["abilities"] == ARCHETYPES["rogue"].abilities  # no bump
    assert sheet["background"] == "Wandering Mage"
    assert sheet["id"] == "aria"  # slugified from name when pc_id omitted


def test_run_wizard_rejects_blank_name_then_continues() -> None:
    input_fn, print_fn, _ = _scripted(
        "",         # blank, re-asked
        "Hero",
        "warrior",
        "0",        # skip bump
        "sellsword",
        "n",        # skip shopping
        "y",
    )
    sheet = run_wizard("hero", input_fn=input_fn, print_fn=print_fn)
    assert sheet["name"] == "Hero"
    assert sheet["class"] == "Warrior"


def test_run_wizard_loops_on_reject_then_accepts() -> None:
    input_fn, print_fn, _ = _scripted(
        # First pass: archetype 1 (witch, a caster) — shopping no, cantrips/spells skipped, reject
        "X", "1", "0", "1", "n", "", "", "n",
        # Second pass: archetype 2 (rogue, non-caster) — shopping no, accept
        "Y", "2", "0", "1", "n", "y",
    )
    sheet = run_wizard("h", input_fn=input_fn, print_fn=print_fn)
    assert sheet["name"] == "Y"
    assert sheet["class"] == ARCHETYPES["rogue"].label  # 2nd archetype


# --------------------------------------------------------------------- #
# needs_wizard / write_sheet
# --------------------------------------------------------------------- #


def test_needs_wizard_false_without_pc_id(tmp_path: Path) -> None:
    pack = _make_pack(tmp_path, pc_id="hero")
    assert needs_wizard(pack, None) is False
    assert needs_wizard(pack, "") is False


def test_needs_wizard_true_when_no_sheet_or_seed(tmp_path: Path) -> None:
    pack = _make_pack(tmp_path, pc_id="hero")
    assert needs_wizard(pack, "hero") is True


def test_needs_wizard_false_when_seed_exists(tmp_path: Path) -> None:
    pack = _make_pack(tmp_path, pc_id="hero")
    seed = pack.paths.characters_seed / "hero.json"
    seed.write_text(json.dumps({"id": "hero", "name": "Hero"}))
    assert needs_wizard(pack, "hero") is False


def test_write_sheet_then_needs_wizard_false(tmp_path: Path) -> None:
    pack = _make_pack(tmp_path, pc_id="hero")
    sheet = build_sheet("hero", "Hero", "warrior", None, "sellsword")
    path = write_sheet(pack, "hero", sheet)
    assert path == sheet_path(pack, "hero")
    assert path.exists()
    written = json.loads(path.read_text())
    assert written["name"] == "Hero"
    assert written["class"] == "Warrior"
    assert needs_wizard(pack, "hero") is False


# --------------------------------------------------------------------- #
# validate_response (used by the remote Foundry-driven wizard)
# --------------------------------------------------------------------- #


def test_validate_response_accepts_full_valid_payload() -> None:
    errs = validate_response({
        "name": "Brigit",
        "archetype": "witch",
        "background": "hedge_witch",
        "stat_bump": "cha",
    })
    assert errs == []


def test_validate_response_allows_empty_stat_bump() -> None:
    errs = validate_response({
        "name": "Brigit",
        "archetype": "witch",
        "background": "hedge_witch",
        "stat_bump": "",
    })
    assert errs == []


def test_validate_response_rejects_blank_name() -> None:
    errs = validate_response({
        "name": " ",
        "archetype": "witch",
        "background": "hedge_witch",
    })
    assert any("Name" in e for e in errs)


def test_validate_response_rejects_unknown_archetype() -> None:
    errs = validate_response({
        "name": "X",  # also short, but archetype error must appear
        "archetype": "bard",
        "background": "hedge_witch",
    })
    assert any("archetype" in e.lower() for e in errs)


def test_validate_response_rejects_unknown_background() -> None:
    errs = validate_response({
        "name": "Brigit",
        "archetype": "witch",
        "background": "circus_clown",
    })
    assert any("background" in e.lower() for e in errs)


def test_validate_response_rejects_unknown_stat_bump() -> None:
    errs = validate_response({
        "name": "Brigit",
        "archetype": "witch",
        "background": "hedge_witch",
        "stat_bump": "luck",
    })
    assert any("stat bump" in e.lower() for e in errs)


def test_validate_response_collects_multiple_errors() -> None:
    errs = validate_response({
        "name": "",
        "archetype": "bard",
        "background": "circus_clown",
    })
    assert len(errs) == 3

