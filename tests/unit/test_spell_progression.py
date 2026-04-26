"""Tests for ``ai_dm.rules.spell_progression`` and the wizard's level-based
spellcasting allotment."""
from __future__ import annotations

import pytest

from ai_dm.app.character_wizard import (
    ARCHETYPES,
    apply_level,
    build_sheet,
)
from ai_dm.rules.spell_progression import (
    casting_style_for,
    class_record,
    progression_for,
    proficiency_bonus_for,
    slots_dict,
    spellcasting_block,
)


# ------------------------------------------------------------- progression --


def test_class_record_known_classes() -> None:
    for key in ("bard", "cleric", "druid", "paladin", "ranger",
                "sorcerer", "warlock", "wizard"):
        rec = class_record(key)
        assert rec is not None, key
        assert rec["ability"] in {"cha", "wis", "int"}
        assert rec["casting_style"] in {"known", "prepared"}
        assert len(rec["progression"]) == 20  # one bucket per level


def test_class_record_unknown_returns_none() -> None:
    assert class_record("monk") is None
    assert class_record("") is None


def test_archetype_aliases() -> None:
    # Wizard archetype keys (from the character wizard) resolve to SRD classes.
    assert class_record("witch")["ability"] == "cha"      # → warlock
    assert class_record("scholar")["ability"] == "int"    # → wizard


def test_casting_style() -> None:
    assert casting_style_for("sorcerer") == "known"
    assert casting_style_for("warlock") == "known"
    assert casting_style_for("wizard") == "prepared"
    assert casting_style_for("cleric") == "prepared"
    assert casting_style_for("paladin") == "prepared"


def test_proficiency_bonus_curve() -> None:
    # Vanilla SRD curve: +2 (1-4), +3 (5-8), +4 (9-12), +5 (13-16), +6 (17-20).
    expected = [
        (1, 2), (4, 2), (5, 3), (8, 3), (9, 4),
        (12, 4), (13, 5), (16, 5), (17, 6), (20, 6),
    ]
    for level, pb in expected:
        assert proficiency_bonus_for("wizard", level) == pb
        assert proficiency_bonus_for("paladin", level) == pb
        assert proficiency_bonus_for("warlock", level) == pb


# --- per-class L1 allotments (spot checks against SRD 5.2.1 tables) -------


def test_wizard_l1_block() -> None:
    block = spellcasting_block("wizard", 1)
    assert block == {
        "ability": "int",
        "cantrips_known": 3,
        "spells_known": 4,         # SRD calls this "Spells" (prepared cap)
        "slots": {"1": 2},
        "casting_style": "prepared",
        "kind": "full",
    }


def test_sorcerer_l1_block() -> None:
    block = spellcasting_block("sorcerer", 1)
    assert block["ability"] == "cha"
    assert block["cantrips_known"] == 4
    assert block["spells_known"] == 2
    assert block["slots"] == {"1": 2}
    assert block["casting_style"] == "known"


def test_warlock_l1_block_uses_pact_slots() -> None:
    block = spellcasting_block("warlock", 1)
    assert block["ability"] == "cha"
    assert block["cantrips_known"] == 2
    assert block["spells_known"] == 2
    assert block["slots"] == {"1": 1}
    assert block["pact_slots"] == {"count": 1, "level": 1}
    assert block["invocations"] == 1


def test_paladin_l1_no_cantrips_two_l1_slots() -> None:
    block = spellcasting_block("paladin", 1)
    assert block["cantrips_known"] == 0
    assert block["spells_known"] == 2
    assert block["slots"] == {"1": 2}


# --- mid-level scaling -----------------------------------------------------


def test_wizard_l5_slots_match_srd() -> None:
    block = spellcasting_block("wizard", 5)
    assert block["cantrips_known"] == 4
    assert block["spells_known"] == 9
    assert block["slots"] == {"1": 4, "2": 3, "3": 2}


def test_warlock_l5_pact_grows() -> None:
    block = spellcasting_block("warlock", 5)
    # SRD warlock 5: 2 slots at level 3 (Pact Magic).
    assert block["pact_slots"] == {"count": 2, "level": 3}
    assert block["slots"] == {"3": 2}
    assert block["invocations"] == 5


def test_full_caster_l20_has_nine_levels() -> None:
    block = spellcasting_block("wizard", 20)
    assert set(block["slots"]) == {"1", "2", "3", "4", "5", "6", "7", "8", "9"}
    # SRD wizard 20 last row: 4/3/3/3/3/2/2/1/1.
    assert [block["slots"][str(i)] for i in range(1, 10)] == [4, 3, 3, 3, 3, 2, 2, 1, 1]


def test_half_caster_capped_at_five_slot_levels() -> None:
    # Paladin / ranger never get level 6+ slots in SRD.
    for level in (1, 5, 10, 15, 20):
        for cls in ("paladin", "ranger"):
            keys = set(slots_dict(cls, level))
            assert keys.issubset({"1", "2", "3", "4", "5"}), (cls, level, keys)


def test_level_clamping() -> None:
    # Out-of-range levels clamp to the nearest valid bucket.
    assert progression_for("wizard", 0) == progression_for("wizard", 1)
    assert progression_for("wizard", 99) == progression_for("wizard", 20)


# --- character-wizard integration -----------------------------------------


def test_build_sheet_witch_l1_uses_warlock_progression() -> None:
    sheet = build_sheet("h", "H", "witch", None, "exiled_noble")
    sc = sheet["spellcasting"]
    assert sc["ability"] == "cha"
    assert sc["cantrips_known"] == 2          # warlock L1
    assert sc["spells_known"] == 2            # warlock L1
    assert sc["slots"] == {"1": 1}            # warlock L1 pact slot
    assert sc["pact_slots"] == {"count": 1, "level": 1}
    assert sheet["proficiency_bonus"] == 2


def test_build_sheet_scholar_l1_uses_wizard_progression() -> None:
    sheet = build_sheet("h", "H", "scholar", None, "exiled_noble")
    sc = sheet["spellcasting"]
    assert sc["ability"] == "int"
    assert sc["cantrips_known"] == 3          # wizard L1
    assert sc["spells_known"] == 4            # wizard L1 prepared cap
    assert sc["slots"] == {"1": 2}            # wizard L1


def test_build_sheet_warrior_has_no_spellcasting() -> None:
    sheet = build_sheet("h", "H", "warrior", None, "sellsword")
    assert sheet["spellcasting"] is None
    assert sheet["spells"] == {
        "cantrips_known": [], "prepared": [], "known": [], "slots": {}
    }
    # Default proficiency bonus for a non-caster L1.
    assert sheet["proficiency_bonus"] == 2


def test_build_sheet_explicit_level_grows_allotment() -> None:
    sheet = build_sheet(
        "h", "H", "scholar", None, "exiled_noble", level=5,
    )
    sc = sheet["spellcasting"]
    assert sheet["level"] == 5
    assert sheet["proficiency_bonus"] == 3
    # Wizard L5: 4 cantrips, 9 spells prepared, slots {1:4, 2:3, 3:2}.
    assert sc["cantrips_known"] == 4
    assert sc["spells_known"] == 9
    assert sc["slots"] == {"1": 4, "2": 3, "3": 2}


def test_build_sheet_witch_at_level_5_pact_magic() -> None:
    sheet = build_sheet("h", "H", "witch", None, "exiled_noble", level=5)
    sc = sheet["spellcasting"]
    assert sc["pact_slots"] == {"count": 2, "level": 3}
    assert sc["slots"] == {"3": 2}
    assert sc["cantrips_known"] == 3
    assert sc["spells_known"] == 6


def test_build_sheet_clamps_extreme_levels() -> None:
    # Levels outside 1..20 are clamped — sheet "level" itself is clamped too
    # so callers can't accidentally serialise a 99 onto disk.
    sheet = build_sheet("h", "H", "scholar", None, "exiled_noble", level=99)
    assert sheet["level"] == 20
    sc = sheet["spellcasting"]
    assert [sc["slots"][str(i)] for i in range(1, 10)] == [4, 3, 3, 3, 3, 2, 2, 1, 1]


# --- apply_level (level-up) -----------------------------------------------


def test_apply_level_grows_witch_to_5() -> None:
    sheet = build_sheet("h", "H", "witch", None, "exiled_noble")
    leveled = apply_level(sheet, 5)
    assert leveled["level"] == 5
    assert leveled["proficiency_bonus"] == 3
    assert leveled["spellcasting"]["cantrips_known"] == 3
    assert leveled["spellcasting"]["pact_slots"] == {"count": 2, "level": 3}
    # Slot maxes track the new progression and are refilled on level-up.
    assert leveled["spells"]["slots"] == {"3": {"max": 2, "value": 2}}


def test_apply_level_idempotent_at_same_level() -> None:
    sheet = build_sheet("h", "H", "scholar", None, "exiled_noble")
    again = apply_level(sheet, 1)
    assert again["level"] == 1
    assert again["spellcasting"] == sheet["spellcasting"]
    assert again["proficiency_bonus"] == sheet["proficiency_bonus"]


def test_apply_level_preserves_known_spells() -> None:
    sheet = build_sheet(
        "h", "H", "witch", None, "exiled_noble",
        cantrip_picks=["fire_bolt"],   # may or may not be in default catalog
        spell_picks=[],
    )
    cantrips_before = list(sheet["spells"]["cantrips_known"])
    leveled = apply_level(sheet, 3)
    # Known/prepared lists are preserved verbatim (level-up never forgets).
    assert leveled["spells"]["cantrips_known"] == cantrips_before


def test_apply_level_for_non_caster() -> None:
    sheet = build_sheet("h", "H", "warrior", None, "sellsword")
    leveled = apply_level(sheet, 9)
    assert leveled["level"] == 9
    assert leveled["proficiency_bonus"] == 4   # default curve: +4 at 9-12
    assert leveled["spellcasting"] is None


def test_apply_level_clamps_out_of_range() -> None:
    sheet = build_sheet("h", "H", "scholar", None, "exiled_noble")
    assert apply_level(sheet, 0)["level"] == 1
    assert apply_level(sheet, 999)["level"] == 20


# --- consistency across all eight SRD spellcasters -------------------------


@pytest.mark.parametrize(
    "class_key", ["bard", "cleric", "druid", "paladin",
                  "ranger", "sorcerer", "warlock", "wizard"],
)
def test_every_caster_has_at_least_one_l1_slot(class_key: str) -> None:
    block = spellcasting_block(class_key, 1)
    assert block is not None
    assert sum(block["slots"].values()) >= 1


@pytest.mark.parametrize("level", list(range(1, 21)))
def test_warlock_pact_slot_level_monotone(level: int) -> None:
    block = spellcasting_block("warlock", level)
    assert block["pact_slots"]["level"] >= 1
    assert block["pact_slots"]["count"] >= 1


def test_archetype_class_keys_resolve() -> None:
    # Every archetype with a class_key must produce a valid spellcasting
    # block at level 1 — guards against a future archetype being added
    # with a typo'd class key.
    for arch in ARCHETYPES.values():
        if arch.class_key is None:
            continue
        assert spellcasting_block(arch.class_key, 1) is not None, arch.key

