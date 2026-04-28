#!/usr/bin/env python3
"""Validate the SRD 5.2.1 core JSON catalog and its Foundry mirror.

Checks (all must pass; non-zero exit on any failure):

  1. Every file under ``assets/srd5_2/core/`` parses as JSON.
  2. Every mechanics file matches a hard-coded shape contract
     (key presence, list lengths, value ranges) — drift here would
     silently break the rules engine.
  3. The Foundry mirror at ``foundry/module/assets/srd5_2/core/``
     is byte-equal to the canonical files (run
     ``scripts/sync_foundry_assets.py`` to fix).
  4. ``assets/srd5_2/progression.json`` per-class proficiency-bonus
     columns agree with ``core/proficiency.json["by_level"]``
     (catches the dual-source drift risk).

Run from the repo root::

    python scripts/validate_assets.py
"""
from __future__ import annotations

import filecmp
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "assets" / "srd5_2" / "core"
MIRROR = REPO / "foundry" / "module" / "assets" / "srd5_2" / "core"
PROGRESSION = REPO / "assets" / "srd5_2" / "progression.json"

# Files that the rules engine + Foundry mirror both depend on.
MECHANICS_FILES = (
    "abilities",
    "proficiency",
    "dcs",
    "damage_types",
    "conditions",
    "exhaustion",
    "death_saves",
    "initiative",
    "turn_structure",
    "actions",
    "movement",
    "opportunity_attacks",
    "cover",
    "stealth",
    "grapple_shove",
    "concentration",
    "areas_of_effect",
    "rests",
    "currency",
    "weapon_properties",
    "weapon_mastery",
    "weapons",
    "armor",
    "adventuring_gear",
    "tools",
    "mounts_vehicles",
    "encumbrance",
    "attunement",
)

errors: list[str] = []


def _err(msg: str) -> None:
    errors.append(msg)
    print(f"  ✗ {msg}", file=sys.stderr)


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


# --------------------------------------------------------------------- #
# Schema contracts (intentionally inline — no external dependency)
# --------------------------------------------------------------------- #


def _check_abilities(d: dict) -> None:
    abs_ = d.get("abilities")
    assert isinstance(abs_, list) and abs_ == ["str", "dex", "con", "int", "wis", "cha"], \
        f"abilities.json: must list the six SRD ability keys in order, got {abs_!r}"
    assert isinstance(d.get("score_min"), int) and d["score_min"] >= 1
    assert isinstance(d.get("score_max"), int) and d["score_max"] >= d["score_min"]


def _check_proficiency(d: dict) -> None:
    by = d.get("by_level")
    assert isinstance(by, list) and len(by) == 20, \
        f"proficiency.json: by_level must have 20 entries, got {len(by) if isinstance(by, list) else 'n/a'}"
    expected = [2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 6, 6, 6, 6]
    assert by == expected, f"proficiency.json: by_level diverges from SRD curve\n  got {by}\n  exp {expected}"


def _check_dcs(d: dict) -> None:
    named = d.get("named")
    assert isinstance(named, dict) and named, "dcs.json: 'named' must be a non-empty dict"
    for k in ("very_easy", "easy", "medium", "hard", "very_hard"):
        assert k in named, f"dcs.json: missing required named DC {k!r}"
        assert isinstance(named[k], int)


def _check_damage_types(d: dict) -> None:
    types = d.get("types")
    assert isinstance(types, list) and len(types) == 13, \
        f"damage_types.json: SRD has 13 damage types, got {len(types) if isinstance(types, list) else 'n/a'}"


def _check_conditions(d: dict) -> None:
    cs = d.get("conditions")
    assert isinstance(cs, list) and len(cs) == 15, \
        f"conditions.json: SRD 5.2.1 has 15 conditions (incl. exhaustion), got {len(cs) if isinstance(cs, list) else 'n/a'}"
    seen = set()
    for rec in cs:
        assert isinstance(rec, dict) and "key" in rec and "label" in rec, \
            f"conditions.json: each entry needs 'key' + 'label', bad: {rec!r}"
        assert rec["key"] not in seen, f"conditions.json: duplicate key {rec['key']!r}"
        seen.add(rec["key"])


def _check_exhaustion(d: dict) -> None:
    assert d.get("max_level") == 6, "exhaustion.json: SRD 5.2.1 caps exhaustion at level 6"
    assert d.get("death_at") == 6
    per = d.get("per_level") or {}
    assert per.get("d20_penalty") == -2
    assert per.get("speed_penalty_ft") == -5


def _check_death_saves(d: dict) -> None:
    for k, v in (
        ("dc", 10),
        ("successes_to_stable", 3),
        ("failures_to_die", 3),
        ("nat20_heals_to", 1),
        ("nat1_failures", 2),
        ("damage_at_zero_failures", 1),
        ("crit_at_zero_failures", 2),
        ("massive_damage_threshold_factor", 2),
    ):
        assert d.get(k) == v, f"death_saves.json: {k} must be {v}, got {d.get(k)!r}"


def _check_initiative(d: dict) -> None:
    assert d.get("ability") == "dex", "initiative.json: ability must be 'dex'"
    assert isinstance(d.get("surprise"), dict), "initiative.json: missing 'surprise' block"
    assert isinstance(d["surprise"].get("skip_first_turn"), bool)


def _check_turn_structure(d: dict) -> None:
    phases = d.get("phases")
    assert isinstance(phases, list) and phases[0] == "start_of_turn" and phases[-1] == "end_of_turn", \
        f"turn_structure.json: phases must start at 'start_of_turn' and end at 'end_of_turn', got {phases!r}"
    assert isinstance(d.get("free_object_interactions_per_turn"), int)
    assert d.get("reaction_resets_at") in ("start_of_turn", "start_of_round")


def _check_actions(d: dict) -> None:
    keys = d.get("economy_keys")
    assert keys == ["action", "bonus_action", "reaction", "free"], \
        f"actions.json: economy_keys must be the four canonical keys, got {keys!r}"
    acts = d.get("standard_actions")
    assert isinstance(acts, list) and acts, "actions.json: standard_actions must be non-empty"
    seen = set()
    for rec in acts:
        assert isinstance(rec, dict) and "key" in rec and "economy" in rec, f"actions.json: bad record {rec!r}"
        assert rec["economy"] in keys, f"actions.json: unknown economy {rec['economy']!r}"
        assert rec["key"] not in seen, f"actions.json: duplicate action key {rec['key']!r}"
        seen.add(rec["key"])
    for required in ("attack", "dash", "dodge", "disengage", "hide", "ready", "help", "grapple", "shove"):
        assert required in seen, f"actions.json: missing required action {required!r}"


def _check_movement(d: dict) -> None:
    assert isinstance(d.get("default_speed_ft"), int) and d["default_speed_ft"] > 0
    assert d.get("difficult_terrain_factor") == 2
    assert d.get("prone_crawl_factor") == 2
    modes = d.get("modes")
    assert isinstance(modes, list) and "walk" in modes


def _check_opportunity_attacks(d: dict) -> None:
    assert d.get("uses") == "reaction", "opportunity_attacks.json: must use a reaction"
    blockers = d.get("blockers")
    assert isinstance(blockers, list) and "disengaging" in blockers


def _check_cover(d: dict) -> None:
    levels = d.get("levels")
    assert isinstance(levels, list) and len(levels) == 4, \
        f"cover.json: SRD has 4 cover levels, got {len(levels) if isinstance(levels, list) else 'n/a'}"
    keys = [l["key"] for l in levels]
    assert keys == ["none", "half", "three_quarters", "total"], \
        f"cover.json: levels must be none/half/three_quarters/total in order, got {keys!r}"
    half = next(l for l in levels if l["key"] == "half")
    assert half.get("ac") == 2 and half.get("save") == 2
    tq = next(l for l in levels if l["key"] == "three_quarters")
    assert tq.get("ac") == 5 and tq.get("save") == 5
    total = next(l for l in levels if l["key"] == "total")
    assert total.get("blocks") is True


def _check_stealth(d: dict) -> None:
    triggers = d.get("break_triggers")
    assert isinstance(triggers, list) and "attack" in triggers and "cast_spell" in triggers
    assert isinstance(d.get("invisible_grants"), dict)


def _check_grapple_shove(d: dict) -> None:
    g = d.get("grapple")
    assert isinstance(g, dict) and g.get("max_size_diff") == 1
    s = d.get("shove")
    assert isinstance(s, dict) and "push_5ft" in (s.get("options") or []) and "prone" in s["options"]


def _check_concentration(d: dict) -> None:
    assert d.get("save") == "con"
    assert d.get("min_dc") == 10
    assert d.get("auto_drop_at_zero_hp") is True
    assert isinstance(d.get("broken_by"), list)


def _check_areas_of_effect(d: dict) -> None:
    shapes = d.get("shapes")
    assert isinstance(shapes, list) and len(shapes) == 5, \
        f"areas_of_effect.json: SRD has 5 shapes, got {len(shapes) if isinstance(shapes, list) else 'n/a'}"
    keys = {s["key"] for s in shapes}
    assert keys == {"sphere", "cube", "cone", "line", "cylinder"}, \
        f"areas_of_effect.json: unexpected shape set {keys!r}"


def _check_rests(d: dict) -> None:
    sr = d.get("short_rest")
    lr = d.get("long_rest")
    assert isinstance(sr, dict) and sr.get("duration_min") == 60
    assert isinstance(lr, dict) and lr.get("duration_hr") == 8
    assert lr.get("max_per_day") == 1
    rec = lr.get("recovers") or []
    assert "hp_full" in rec and "spell_slots" in rec


# --- Equipment-layer contracts -------------------------------------- #

_COIN_KEYS = ("cp", "sp", "ep", "gp", "pp")
_WEAPON_CATEGORIES = {"simple_melee", "simple_ranged", "martial_melee", "martial_ranged"}
_ARMOR_CATEGORIES = {"light", "medium", "heavy", "shield"}
_ARMOR_DEX_MODES = {"add", "add_max_2", "none", "flat"}
_GEAR_CATEGORIES = {"gear", "pack", "ammunition", "focus", "consumable", "container"}
_TOOL_CATEGORIES = {"artisan", "gaming", "musical", "kit"}
_MOUNT_KINDS = {"mount", "draft", "vehicle_land", "vehicle_water", "tack"}
_DAMAGE_DICE_RE = re.compile(r"^\d+(d\d+)?$")
_MASTERY_KEYS_2024 = {"cleave", "graze", "nick", "push", "sap", "slow", "topple", "vex"}


def _check_currency(d: dict) -> None:
    coins = d.get("coins")
    assert isinstance(coins, list) and len(coins) == 5, "currency.json: must list 5 coin denominations"
    by_key = {c["key"]: c for c in coins}
    assert tuple(by_key.keys()) == _COIN_KEYS, \
        f"currency.json: coins must be in cp,sp,ep,gp,pp order, got {tuple(by_key.keys())!r}"
    assert by_key["cp"]["gp_value"] == 0.01
    assert by_key["sp"]["gp_value"] == 0.1
    assert by_key["ep"]["gp_value"] == 0.5
    assert by_key["gp"]["gp_value"] == 1
    assert by_key["pp"]["gp_value"] == 10
    assert d.get("coins_per_pound") == 50, "currency.json: SRD says 50 coins per pound"


def _check_weapon_properties(d: dict) -> None:
    props = d.get("properties")
    assert isinstance(props, list) and props, "weapon_properties.json: 'properties' must be non-empty"
    keys = {p["key"] for p in props}
    expected = {"ammunition", "finesse", "heavy", "light", "loading", "range", "reach",
                "thrown", "two_handed", "versatile"}
    assert keys == expected, f"weapon_properties.json: unexpected key set {keys ^ expected}"


def _check_weapon_mastery(d: dict) -> None:
    masteries = d.get("masteries")
    assert isinstance(masteries, list) and len(masteries) == 8, \
        "weapon_mastery.json: 2024 SRD has exactly 8 mastery properties"
    keys = {m["key"] for m in masteries}
    assert keys == _MASTERY_KEYS_2024, \
        f"weapon_mastery.json: unexpected mastery set {keys ^ _MASTERY_KEYS_2024}"
    cp = d.get("class_progression") or {}
    assert "fighter" in cp and isinstance(cp["fighter"], dict) and cp["fighter"].get("1") == 3, \
        "weapon_mastery.json: fighter L1 must learn 3 masteries"


def _check_weapons(d: dict) -> None:
    weapons = d.get("weapons")
    assert isinstance(weapons, list) and weapons, "weapons.json: 'weapons' list missing"
    seen: set[str] = set()
    for w in weapons:
        key = w.get("key")
        assert isinstance(key, str) and key not in seen, f"weapons.json: dup/missing key {key!r}"
        seen.add(key)
        assert w.get("category") in _WEAPON_CATEGORIES, \
            f"weapons.json[{key}]: bad category {w.get('category')!r}"
        cost = w.get("cost") or {}
        assert isinstance(cost.get("amount"), (int, float)) and cost.get("unit") in _COIN_KEYS, \
            f"weapons.json[{key}]: bad cost {cost!r}"
        assert isinstance(w.get("weight"), (int, float)) and w["weight"] >= 0
        dmg = w.get("damage") or {}
        assert _DAMAGE_DICE_RE.match(str(dmg.get("dice") or "")), \
            f"weapons.json[{key}]: bad damage.dice {dmg.get('dice')!r}"
        if "versatile" in dmg:
            assert _DAMAGE_DICE_RE.match(str(dmg["versatile"]))
            assert "versatile" in (w.get("properties") or []), \
                f"weapons.json[{key}]: versatile die without 'versatile' property"
        rng = w.get("range")
        if rng is not None:
            assert isinstance(rng.get("normal"), (int, float)) and isinstance(rng.get("long"), (int, float))
            assert rng["normal"] <= rng["long"]
        mast = w.get("mastery")
        assert mast is None or mast in _MASTERY_KEYS_2024, \
            f"weapons.json[{key}]: unknown mastery {mast!r}"
    by_key = {w["key"]: w for w in weapons}
    assert by_key["longsword"]["damage"] == {"dice": "1d8", "type": "slashing", "versatile": "1d10"}
    assert by_key["longsword"]["mastery"] == "sap"
    assert by_key["greataxe"]["mastery"] == "cleave"
    assert by_key["dagger"]["mastery"] == "nick"


def _check_armor(d: dict) -> None:
    armors = d.get("armors")
    assert isinstance(armors, list) and armors, "armor.json: 'armors' missing"
    seen: set[str] = set()
    by_key: dict[str, dict] = {}
    for a in armors:
        key = a.get("key")
        assert isinstance(key, str) and key not in seen, f"armor.json: dup/missing key {key!r}"
        seen.add(key)
        by_key[key] = a
        assert a.get("category") in _ARMOR_CATEGORIES, f"armor.json[{key}]: bad category"
        ac = a.get("ac") or {}
        assert isinstance(ac.get("base"), int)
        assert ac.get("dex") in _ARMOR_DEX_MODES, f"armor.json[{key}]: bad ac.dex {ac.get('dex')!r}"
        assert isinstance(a.get("stealth_disadvantage"), bool)
        sr = a.get("strength_req")
        assert sr is None or isinstance(sr, int)
    plate = by_key["plate"]
    assert plate["ac"]["base"] == 18 and plate["ac"]["dex"] == "none"
    assert plate["strength_req"] == 15 and plate["stealth_disadvantage"] is True
    shield = by_key["shield"]
    assert shield["ac"]["base"] == 2 and shield["ac"]["dex"] == "flat"


def _check_adventuring_gear(d: dict) -> None:
    items = d.get("items")
    assert isinstance(items, list) and items, "adventuring_gear.json: 'items' missing"
    keys = {it["key"] for it in items}
    assert len(keys) == len(items), "adventuring_gear.json: duplicate item keys"
    for it in items:
        assert it.get("category") in _GEAR_CATEGORIES, \
            f"adventuring_gear.json[{it.get('key')}]: bad category"
        assert (it.get("cost") or {}).get("unit") in _COIN_KEYS
        assert isinstance(it.get("weight"), (int, float))
        if it["category"] == "pack":
            contents = it.get("contents") or []
            assert isinstance(contents, list) and contents, \
                f"adventuring_gear.json[{it['key']}]: pack needs contents"
            for ref in contents:
                assert ref.get("ref") in keys, \
                    f"adventuring_gear.json[{it['key']}]: unresolved ref {ref!r}"


def _check_tools(d: dict) -> None:
    tools = d.get("tools")
    assert isinstance(tools, list) and tools
    seen: set[str] = set()
    for t in tools:
        key = t.get("key")
        assert isinstance(key, str) and key not in seen
        seen.add(key)
        assert t.get("category") in _TOOL_CATEGORIES, f"tools.json[{key}]: bad category"
        assert (t.get("cost") or {}).get("unit") in _COIN_KEYS
    for required in ("thieves_tools", "herbalism_kit", "smiths_tools", "lute"):
        assert required in seen, f"tools.json: missing canonical tool {required!r}"


def _check_mounts_vehicles(d: dict) -> None:
    entries = d.get("entries")
    assert isinstance(entries, list) and entries
    seen: set[str] = set()
    for e in entries:
        key = e.get("key")
        assert isinstance(key, str) and key not in seen
        seen.add(key)
        assert e.get("kind") in _MOUNT_KINDS, f"mounts_vehicles.json[{key}]: bad kind"
        assert (e.get("cost") or {}).get("unit") in _COIN_KEYS


def _check_encumbrance(d: dict) -> None:
    assert d.get("carrying_capacity_per_str") == 15
    assert d.get("push_drag_lift_per_str") == 30
    v = d.get("variant") or {}
    assert v.get("encumbered_per_str") == 5
    assert v.get("heavily_encumbered_per_str") == 10
    assert v.get("max_per_str") == 15
    assert v.get("encumbered_speed_penalty_ft") == -10
    assert v.get("heavily_encumbered_speed_penalty_ft") == -20
    assert isinstance(v.get("heavily_encumbered_disadvantage"), list) and v["heavily_encumbered_disadvantage"]


def _check_attunement(d: dict) -> None:
    assert d.get("max_attuned") == 3, "attunement.json: SRD caps attunement at 3 items"
    assert d.get("short_rest_to_attune_min") == 60



CHECKS: dict[str, Callable[[dict], None]] = {
    "abilities": _check_abilities,
    "proficiency": _check_proficiency,
    "dcs": _check_dcs,
    "damage_types": _check_damage_types,
    "conditions": _check_conditions,
    "exhaustion": _check_exhaustion,
    "death_saves": _check_death_saves,
    "initiative": _check_initiative,
    "turn_structure": _check_turn_structure,
    "actions": _check_actions,
    "movement": _check_movement,
    "opportunity_attacks": _check_opportunity_attacks,
    "cover": _check_cover,
    "stealth": _check_stealth,
    "grapple_shove": _check_grapple_shove,
    "concentration": _check_concentration,
    "areas_of_effect": _check_areas_of_effect,
    "rests": _check_rests,
    "currency": _check_currency,
    "weapon_properties": _check_weapon_properties,
    "weapon_mastery": _check_weapon_mastery,
    "weapons": _check_weapons,
    "armor": _check_armor,
    "adventuring_gear": _check_adventuring_gear,
    "tools": _check_tools,
    "mounts_vehicles": _check_mounts_vehicles,
    "encumbrance": _check_encumbrance,
    "attunement": _check_attunement,
}


# --------------------------------------------------------------------- #
# Drivers
# --------------------------------------------------------------------- #


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _err(f"{path.relative_to(REPO)}: invalid JSON — {e}")
        return None


def check_parse_all() -> None:
    print("[1/4] parsing every JSON under assets/srd5_2/core/ ...")
    for path in sorted(CORE.glob("*.json")):
        if _load(path) is not None:
            _ok(f"parsed {path.name}")


def check_mechanics_shapes() -> None:
    print("[2/4] checking mechanics file shapes ...")
    for name in MECHANICS_FILES:
        path = CORE / f"{name}.json"
        data = _load(path)
        if data is None:
            continue
        try:
            CHECKS[name](data)
            _ok(f"shape ok: {name}.json")
        except AssertionError as e:
            _err(str(e))


def check_foundry_mirror() -> None:
    print("[3/4] checking Foundry mirror byte-equality ...")
    if not MIRROR.exists():
        _err(f"missing mirror dir: {MIRROR.relative_to(REPO)} — run scripts/sync_foundry_assets.py")
        return
    for name in MECHANICS_FILES:
        src = CORE / f"{name}.json"
        dst = MIRROR / f"{name}.json"
        if not dst.exists():
            _err(f"missing mirror file: {dst.relative_to(REPO)} — run scripts/sync_foundry_assets.py")
            continue
        if not filecmp.cmp(src, dst, shallow=False):
            _err(f"mirror drift: {dst.relative_to(REPO)} — run scripts/sync_foundry_assets.py")
        else:
            _ok(f"mirror in sync: {name}.json")


def check_progression_pb_agrees() -> None:
    print("[4/4] checking progression.json per-class PB vs core/proficiency.json ...")
    prog = _load(PROGRESSION)
    core_pb = _load(CORE / "proficiency.json")
    if prog is None or core_pb is None:
        return
    table = core_pb["by_level"]
    classes = (prog.get("classes") or {})
    if not classes:
        _err("progression.json: no 'classes' key")
        return
    bad = 0
    for cls, body in classes.items():
        rows = (body or {}).get("progression") or {}
        for lvl_str, row in rows.items():
            try:
                lvl = int(lvl_str)
            except ValueError:
                continue
            pb = row.get("proficiency_bonus")
            if pb is None:
                continue
            expected = table[lvl - 1]
            if pb != expected:
                _err(
                    f"progression.json[{cls}][L{lvl}].proficiency_bonus={pb} "
                    f"!= core/proficiency.json[{lvl - 1}]={expected}"
                )
                bad += 1
    if bad == 0:
        _ok("all per-class proficiency bonuses agree with core/proficiency.json")


def main() -> int:
    if not CORE.exists():
        print(f"missing core dir: {CORE}", file=sys.stderr)
        return 2
    check_parse_all()
    check_mechanics_shapes()
    check_foundry_mirror()
    check_progression_pb_agrees()
    if errors:
        print(f"\n✗ {len(errors)} validation error(s).", file=sys.stderr)
        return 1
    print("\n✓ all SRD asset checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
