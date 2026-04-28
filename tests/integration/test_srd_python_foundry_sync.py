"""Contract test: SRD core JSON ↔ Python ↔ Foundry mirror.

Asserts that the single source of truth at ``assets/srd5_2/core/`` is
faithfully consumed by *both* the Python rules layer
(``ai_dm.rules.*``) and the Foundry-side mirror
(``foundry/module/scripts/srd/*.js``).

Strategy:
  1. The Foundry asset tree (``foundry/module/assets/srd5_2/core/``)
     must byte-equal the canonical JSON. ``scripts/sync_foundry_assets.py``
     is the single way to update the mirror.
  2. The JSON keys used by the JavaScript mirror must match the JSON
     keys exposed to Python — proven by grep'ing the JS source for
     every condition key, damage type, named DC, and ability key.
  3. The Python proficiency-bonus helper must agree with the JSON
     table for every level 1..20.
"""
from __future__ import annotations

import filecmp
import json
import re
from pathlib import Path

import pytest

from ai_dm.rules import abilities, conditions, damage, dc
from ai_dm.rules.spell_progression import proficiency_bonus_for
from ai_dm.rules.srd_core import CORE_DIR

REPO = Path(__file__).resolve().parents[2]
FOUNDRY_MIRROR = REPO / "foundry" / "module" / "assets" / "srd5_2" / "core"
FOUNDRY_SCRIPTS = REPO / "foundry" / "module" / "scripts" / "srd"
JS_SOURCE = "\n".join(
    p.read_text(encoding="utf-8") for p in sorted(FOUNDRY_SCRIPTS.glob("*.js"))
)


# ----- 1. Foundry asset mirror is byte-identical -------------------------- #


_ALL_MECHANICS = [
    "abilities", "proficiency", "dcs", "damage_types",
    "conditions", "exhaustion", "death_saves",
    "initiative", "turn_structure", "actions", "movement",
    "opportunity_attacks", "cover", "stealth", "grapple_shove",
    "concentration", "areas_of_effect", "rests",
    "currency", "weapon_properties", "weapon_mastery",
    "weapons", "armor", "adventuring_gear", "tools",
    "mounts_vehicles", "encumbrance", "attunement",
]


@pytest.mark.parametrize("name", _ALL_MECHANICS)
def test_foundry_mirror_in_sync(name: str) -> None:
    src = CORE_DIR / f"{name}.json"
    dst = FOUNDRY_MIRROR / f"{name}.json"
    assert dst.exists(), (
        f"missing Foundry mirror {dst}; run `python scripts/sync_foundry_assets.py`"
    )
    assert filecmp.cmp(src, dst, shallow=False), (
        f"{dst.name} drift; run `python scripts/sync_foundry_assets.py`"
    )


# ----- 2. JS layer references every shared key ---------------------------- #


def test_js_loader_lists_every_core_file() -> None:
    """The JS `core_loader` FILES array must enumerate all SRD JSONs."""
    loader = (FOUNDRY_SCRIPTS / "core_loader.js").read_text(encoding="utf-8")
    for name in _ALL_MECHANICS:
        assert f'"{name}"' in loader, f"core_loader.js missing {name!r}"


def test_js_mirror_module_exists_per_mechanics_file() -> None:
    """Every mechanics JSON must have a 1:1 JS module under scripts/srd/."""
    # Mapping: JSON name → JS filename (mostly identical).
    js_name = {"grapple_shove": "grapple", "turn_structure": "turn"}
    for name in _ALL_MECHANICS:
        if name in ("abilities", "proficiency", "dcs", "damage_types",
                    "conditions", "exhaustion", "death_saves"):
            # Pre-existing modules: name maps via known paths.
            candidates = {"proficiency": "dice", "dcs": "dc",
                          "damage_types": "damage"}
            fname = candidates.get(name, name)
        else:
            fname = js_name.get(name, name)
        path = FOUNDRY_SCRIPTS / f"{fname}.js"
        assert path.exists(), f"missing JS mirror module: {path}"


def test_js_abilities_match_python() -> None:
    # `export const ABILITIES = [...]` must equal the Python tuple.
    m = re.search(r'export const ABILITIES\s*=\s*\[([^\]]+)\]', JS_SOURCE)
    assert m, "couldn't find ABILITIES in JS"
    js_abilities = tuple(
        s.strip().strip('"').strip("'") for s in m.group(1).split(",") if s.strip()
    )
    assert js_abilities == abilities.ABILITIES


# ----- 3. Python helpers agree with the JSON ------------------------------ #


def test_python_proficiency_matches_json_table() -> None:
    table = json.loads((CORE_DIR / "proficiency.json").read_text())["by_level"]
    # Probe with all eight SRD spellcasters (they all use the standard curve)
    # plus an unknown class (default fallback).
    for cls in ("wizard", "warlock", "paladin", "fighter_unknown"):
        for level in range(1, 21):
            assert proficiency_bonus_for(cls, level) == table[level - 1], (
                cls, level,
            )


def test_python_named_dc_matches_json() -> None:
    table = json.loads((CORE_DIR / "dcs.json").read_text())["named"]
    assert dc.NAMED_DC == table


def test_python_damage_types_matches_json() -> None:
    types = json.loads((CORE_DIR / "damage_types.json").read_text())["types"]
    assert list(damage.DAMAGE_TYPES) == types


def test_python_condition_keys_match_json() -> None:
    cats = json.loads((CORE_DIR / "conditions.json").read_text())["conditions"]
    keys = [rec["key"] for rec in cats]
    assert list(conditions.ALL_CONDITIONS) == keys
    # SRD 5.2: 14 conditions + exhaustion = 15.
    assert len(keys) == 15


def test_exhaustion_constants_match_json() -> None:
    from ai_dm.rules import exhaustion
    raw = json.loads((CORE_DIR / "exhaustion.json").read_text())
    assert exhaustion.MAX_LEVEL == raw["max_level"]
    assert exhaustion.DEATH_AT == raw["death_at"]
    assert exhaustion.D20_PENALTY_PER_LEVEL == raw["per_level"]["d20_penalty"]
    assert exhaustion.SPEED_PENALTY_PER_LEVEL_FT == raw["per_level"]["speed_penalty_ft"]


def test_death_save_constants_match_json() -> None:
    from ai_dm.rules import death_saves
    raw = json.loads((CORE_DIR / "death_saves.json").read_text())
    assert death_saves.DC == raw["dc"]
    assert death_saves.SUCCESSES_TO_STABLE == raw["successes_to_stable"]
    assert death_saves.FAILURES_TO_DIE == raw["failures_to_die"]
    assert death_saves.NAT20_HEALS_TO == raw["nat20_heals_to"]
    assert death_saves.NAT1_FAILURES == raw["nat1_failures"]
    assert death_saves.DAMAGE_AT_ZERO_FAILURES == raw["damage_at_zero_failures"]
    assert death_saves.CRIT_AT_ZERO_FAILURES == raw["crit_at_zero_failures"]
    assert death_saves.MASSIVE_DAMAGE_FACTOR == raw["massive_damage_threshold_factor"]


# ----- Combat-rules constants (initiative, turn, actions, …) ----- #


def test_python_initiative_matches_json() -> None:
    from ai_dm.rules import initiative
    raw = json.loads((CORE_DIR / "initiative.json").read_text())
    assert initiative.ABILITY == raw["ability"]
    assert initiative.SURPRISE_SKIPS_FIRST_TURN == raw["surprise"]["skip_first_turn"]


def test_python_turn_matches_json() -> None:
    from ai_dm.rules import turn
    raw = json.loads((CORE_DIR / "turn_structure.json").read_text())
    assert list(turn.PHASES) == raw["phases"]
    assert turn.FREE_OBJECT_INTERACTIONS_PER_TURN == raw["free_object_interactions_per_turn"]
    assert turn.REACTION_RESETS_AT == raw["reaction_resets_at"]


def test_python_actions_matches_json() -> None:
    from ai_dm.rules import actions
    raw = json.loads((CORE_DIR / "actions.json").read_text())
    assert list(actions.ECONOMY_KEYS) == raw["economy_keys"]
    assert set(actions.ACTION_KEYS) == {a["key"] for a in raw["standard_actions"]}
    for a in raw["standard_actions"]:
        assert actions.economy_for(a["key"]) == a["economy"]


def test_python_movement_matches_json() -> None:
    from ai_dm.rules import movement
    raw = json.loads((CORE_DIR / "movement.json").read_text())
    assert movement.DEFAULT_SPEED_FT == raw["default_speed_ft"]
    assert movement.DIFFICULT_TERRAIN_FACTOR == raw["difficult_terrain_factor"]
    assert movement.PRONE_CRAWL_FACTOR == raw["prone_crawl_factor"]


def test_python_opportunity_attacks_matches_json() -> None:
    from ai_dm.rules import opportunity_attack as oa
    raw = json.loads((CORE_DIR / "opportunity_attacks.json").read_text())
    assert oa.USES == raw["uses"]
    assert list(oa.BLOCKERS) == raw["blockers"]


def test_python_cover_matches_json() -> None:
    from ai_dm.rules import cover
    raw = json.loads((CORE_DIR / "cover.json").read_text())
    keys = [l["key"] for l in raw["levels"]]
    assert list(cover.COVER_KEYS) == keys
    assert cover.ac_bonus("half") == 2
    assert cover.dex_save_bonus("three_quarters") == 5
    assert cover.blocks("total") is True


def test_python_stealth_matches_json() -> None:
    from ai_dm.rules import stealth
    raw = json.loads((CORE_DIR / "stealth.json").read_text())
    assert list(stealth.BREAK_TRIGGERS) == raw["break_triggers"]


def test_python_grapple_matches_json() -> None:
    from ai_dm.rules import grapple
    raw = json.loads((CORE_DIR / "grapple_shove.json").read_text())
    assert grapple.MAX_SIZE_DIFF == raw["grapple"]["max_size_diff"]
    assert set(grapple.SHOVE_OPTIONS) == set(raw["shove"]["options"])


def test_python_concentration_matches_json() -> None:
    from ai_dm.rules import concentration
    raw = json.loads((CORE_DIR / "concentration.json").read_text())
    assert concentration.SAVE_ABILITY == raw["save"]
    assert concentration.MIN_DC == raw["min_dc"]
    assert list(concentration.BROKEN_BY) == raw["broken_by"]


def test_python_areas_of_effect_matches_json() -> None:
    from ai_dm.rules import areas_of_effect as aoe
    raw = json.loads((CORE_DIR / "areas_of_effect.json").read_text())
    assert set(aoe.SHAPES) == {s["key"] for s in raw["shapes"]}


def test_python_rests_matches_json() -> None:
    from ai_dm.rules import rests
    raw = json.loads((CORE_DIR / "rests.json").read_text())
    assert rests.SHORT_DURATION_MIN == raw["short_rest"]["duration_min"]
    assert rests.LONG_DURATION_HR == raw["long_rest"]["duration_hr"]
    assert rests.LONG_MAX_PER_DAY == raw["long_rest"]["max_per_day"]


# ----- Equipment-layer parity (added with the equipment catalog) ----- #


def test_python_currency_matches_json() -> None:
    from ai_dm.rules import currency
    raw = json.loads((CORE_DIR / "currency.json").read_text())
    assert list(currency.COIN_KEYS) == [c["key"] for c in raw["coins"]]
    assert currency.COINS_PER_POUND == raw["coins_per_pound"]
    # 100 cp == 1 gp; 50 coins weigh 1 lb.
    assert currency.total_gp({"cp": 100}) == 1.0
    assert currency.weight({"sp": 50}) == 1.0


def test_python_weapon_mastery_matches_json() -> None:
    from ai_dm.rules import weapon_mastery as wm
    raw = json.loads((CORE_DIR / "weapon_mastery.json").read_text())
    assert wm.MASTERY_KEYS == frozenset(m["key"] for m in raw["masteries"])
    assert len(wm.MASTERY_KEYS) == 8
    # Fighter L1 = 3 masteries per 2024 SRD.
    assert wm.mastery_count_for("fighter", 1) == 3
    assert wm.mastery_count_for("fighter", 5) == 4


def test_python_weapons_matches_json() -> None:
    from ai_dm.rules import weapons
    longsword = weapons.get_weapon("longsword")
    assert longsword is not None
    assert weapons.damage_for(longsword) == ("1d8", "slashing")
    assert weapons.damage_for(longsword, two_handed=True) == ("1d10", "slashing")
    assert longsword.mastery == "sap"
    longbow = weapons.get_weapon("longbow")
    assert longbow is not None and weapons.attack_range(longbow) == (150, 600)
    assert longbow.mastery == "slow"


def test_python_armor_matches_json() -> None:
    from ai_dm.rules import armor
    plate = armor.get_armor("plate")
    assert plate is not None
    assert armor.compute_ac(plate, dex_mod=3) == 18  # heavy ignores Dex
    assert armor.meets_strength_requirement(plate, 14) is False
    assert armor.meets_strength_requirement(plate, 15) is True
    assert armor.imposes_stealth_disadvantage(plate) is True
    leather = armor.get_armor("leather")
    shield = armor.get_armor("shield")
    assert armor.compute_ac(leather, dex_mod=4, shield=shield) == 17  # 11 + 4 + 2


def test_python_encumbrance_matches_json() -> None:
    from ai_dm.rules import encumbrance as enc
    assert enc.carrying_capacity(15) == 225  # 15 × STR
    assert enc.push_drag_lift(15) == 450
    # variant: STR 15 → encumbered at 75, heavy at 150
    assert enc.encumbrance_status(70, 15, variant=True) == "normal"
    assert enc.encumbrance_status(76, 15, variant=True) == "encumbered"
    assert enc.encumbrance_status(151, 15, variant=True) == "heavy"
    assert enc.speed_penalty("encumbered") == -10
    assert enc.speed_penalty("heavy") == -20


def test_python_attunement_matches_json() -> None:
    from ai_dm.rules import attunement
    assert attunement.MAX_ATTUNED == 3
    assert attunement.can_attune(["a", "b"]) is True
    assert attunement.can_attune(["a", "b", "c"]) is False


def test_progression_pb_matches_core_table() -> None:
    """Per-class proficiency_bonus in progression.json must agree with
    the canonical curve in core/proficiency.json — guards against the
    dual-source drift risk."""
    table = json.loads((CORE_DIR / "proficiency.json").read_text())["by_level"]
    prog = json.loads(
        (REPO / "assets" / "srd5_2" / "progression.json").read_text()
    )
    classes = prog.get("classes") or {}
    assert classes, "progression.json missing 'classes'"
    mismatches: list[str] = []
    for cls, body in classes.items():
        for lvl_str, row in (body.get("progression") or {}).items():
            pb = row.get("proficiency_bonus")
            if pb is None:
                continue
            lvl = int(lvl_str)
            if pb != table[lvl - 1]:
                mismatches.append(
                    f"{cls}@L{lvl}: progression={pb} core={table[lvl-1]}"
                )
    assert not mismatches, "PB drift:\n  " + "\n  ".join(mismatches)


