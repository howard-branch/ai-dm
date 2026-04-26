"""Tests that ``build_sheet`` and ``apply_level`` populate the new SRD
fields (ability mods, saving throws, exhaustion, death saves, damage
modifier vectors)."""
from __future__ import annotations

from ai_dm.app.character_wizard import apply_level, build_sheet


def test_build_sheet_exposes_srd_fields() -> None:
    sheet = build_sheet("h", "H", "warrior", None, "sellsword")
    # Ability mods + saves derived deterministically.
    assert sheet["ability_mods"]["str"] == 3   # 16
    assert sheet["ability_mods"]["con"] == 2   # 15
    # Warrior is proficient in str + con saves at PB +2.
    assert sheet["saving_throws"]["str"] == 3 + 2
    assert sheet["saving_throws"]["con"] == 2 + 2
    assert sheet["saving_throws"]["dex"] == 1   # not proficient
    assert sheet["saving_throw_profs"] == ["str", "con"]
    # SRD survival defaults
    assert sheet["exhaustion"] == 0
    assert sheet["death_saves"] == {
        "successes": 0, "failures": 0, "stable": False, "dead": False,
    }
    assert sheet["resistances"] == sheet["vulnerabilities"] == sheet["immunities"] == []


def test_build_sheet_scholar_int_save_proficient() -> None:
    sheet = build_sheet("h", "H", "scholar", None, "exiled_noble")
    # Scholar (wizard) saves: int + wis. PB +2, INT 16 → +3 mod.
    assert sheet["saving_throws"]["int"] == 3 + 2
    assert sheet["saving_throws"]["wis"] == 2 + 2
    assert sheet["saving_throws"]["cha"] == 0   # mod 0, not prof


def test_apply_level_grows_proficient_saves() -> None:
    sheet = build_sheet("h", "H", "warrior", None, "sellsword")
    leveled = apply_level(sheet, 5)
    # PB grew +2 → +3, so str-save grew by +1 too.
    assert leveled["proficiency_bonus"] == 3
    assert leveled["saving_throws"]["str"] == 3 + 3
    # Non-proficient saves are unchanged.
    assert leveled["saving_throws"]["dex"] == sheet["saving_throws"]["dex"]


def test_apply_level_preserves_srd_state_vectors() -> None:
    sheet = build_sheet("h", "H", "warrior", None, "sellsword")
    sheet["resistances"] = ["fire"]
    sheet["exhaustion"] = 2
    sheet["death_saves"] = {"successes": 1, "failures": 1, "stable": False, "dead": False}
    leveled = apply_level(sheet, 3)
    # Level-up does not silently scrub status vectors.
    assert leveled["resistances"] == ["fire"]
    assert leveled["exhaustion"] == 2
    assert leveled["death_saves"]["successes"] == 1

