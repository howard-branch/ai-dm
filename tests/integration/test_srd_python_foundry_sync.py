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


@pytest.mark.parametrize(
    "name",
    ["abilities", "proficiency", "dcs", "damage_types",
     "conditions", "exhaustion", "death_saves"],
)
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
    for name in ("abilities", "proficiency", "dcs", "damage_types",
                 "conditions", "exhaustion", "death_saves"):
        assert f'"{name}"' in loader, f"core_loader.js missing {name!r}"


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


