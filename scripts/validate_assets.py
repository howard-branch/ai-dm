#!/usr/bin/env python3
"""Validate the SRD 5.2.1 core JSON catalog and its Foundry mirror.

Checks (all must pass; non-zero exit on any failure):

  1. Every file under ``assets/srd5_2/core/`` parses as JSON.
  2. The seven mechanics files match a hard-coded shape contract
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


CHECKS: dict[str, Callable[[dict], None]] = {
    "abilities": _check_abilities,
    "proficiency": _check_proficiency,
    "dcs": _check_dcs,
    "damage_types": _check_damage_types,
    "conditions": _check_conditions,
    "exhaustion": _check_exhaustion,
    "death_saves": _check_death_saves,
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
