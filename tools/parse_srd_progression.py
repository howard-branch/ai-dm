#!/usr/bin/env python3
"""Parse the per-class spell progression tables out of SRD 5.2.1.

The SRD prints a *Class Features* table for each spellcaster.  The trailing
numeric columns of those tables describe what the character is allotted at
each class level: cantrips known, spells prepared/known, and spell slots
per spell level (1-9).  For the warlock the trailing columns instead encode
``invocations | cantrips | spells | pact-slot count | pact-slot level``.

This script reads ``assets/srd5_2/raw/srd_5_2_1.txt`` and emits a structured
``assets/srd5_2/progression.json`` consumed at runtime by
``ai_dm.rules.spell_progression``.

Output schema::

    {
      "_doc": "...",
      "classes": {
        "<class_key>": {
          "ability": "cha",
          "casting_style": "known" | "prepared",
          "kind": "full" | "half" | "warlock",
          "progression": {
            "1":  {"proficiency_bonus": 2,
                   "cantrips_known": 2,
                   "spells_known": 2,           # or spells_prepared
                   "slots": [2,0,0,0,0,0,0,0,0]},
            ...
            "20": {...}
          }
        },
        "warlock": {
          "ability": "cha",
          "casting_style": "known",
          "kind": "warlock",
          "progression": {
            "1": {"proficiency_bonus": 2,
                  "cantrips_known": 2,
                  "spells_known": 2,
                  "invocations": 1,
                  "pact_slots": {"count": 1, "level": 1}},
            ...
          }
        }
      }
    }
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "srd5_2" / "raw" / "srd_5_2_1.txt"
DST = ROOT / "assets" / "srd5_2" / "progression.json"

DASH_RE = re.compile(r"[—–-]")
ROW_RE = re.compile(r"^\s*(\d{1,2})\b\s+\+\d+\b")


# (class_key, ability, casting_style, kind, header_line_number,
#  trailing_numeric_columns, expected_row_count)
#
# ``trailing_numeric_columns`` is the count of numeric/dash tokens at the
# end of each row that we consume.  For full casters that's
# (cantrips, spells, slot1..slot9) = 11.  For half casters (no cantrip
# column) it's (spells, slot1..slot5) = 6.  For warlock it's
# (cantrips, spells, slot_count, slot_level) = 4.
CLASS_TABLES: list[dict[str, Any]] = [
    {"key": "bard",     "ability": "cha", "casting_style": "prepared", "kind": "full",
     "header_line": 1942, "trailing": 11},
    {"key": "cleric",   "ability": "wis", "casting_style": "prepared", "kind": "full",
     "header_line": 2223, "trailing": 11},
    {"key": "druid",    "ability": "wis", "casting_style": "prepared", "kind": "full",
     "header_line": 2511, "trailing": 11},
    {"key": "paladin",  "ability": "cha", "casting_style": "prepared", "kind": "half",
     "header_line": 3200, "trailing": 6},
    {"key": "ranger",   "ability": "wis", "casting_style": "prepared", "kind": "half",
     "header_line": 3471, "trailing": 6},
    {"key": "sorcerer", "ability": "cha", "casting_style": "known",    "kind": "full",
     "header_line": 3875, "trailing": 11},
    {"key": "warlock",  "ability": "cha", "casting_style": "known",    "kind": "warlock",
     "header_line": 4248, "trailing": 4},
    {"key": "wizard",   "ability": "int", "casting_style": "prepared", "kind": "full",
     "header_line": 4624, "trailing": 11},
]


def _load_lines() -> list[str]:
    # NB: the SRD dump contains form-feed (``\f``) characters as page
    # markers.  ``str.splitlines()`` treats those as line breaks, which
    # would shift our line numbers by ~one per page relative to ``awk``;
    # split on ``\n`` only so the constants in :data:`CLASS_TABLES` line
    # up with the file the user inspects with ``awk`` / ``less``.
    return SRC.read_text(encoding="utf-8", errors="replace").split("\n")


def _collect_rows(lines: list[str], header_line: int) -> list[tuple[int, str]]:
    """Return ``[(level, joined_text)]`` for the 20 rows below ``header_line``.

    Some rows wrap onto a continuation line whose leading whitespace lacks a
    level number (e.g. ``"Spellcasting,"`` / ``"Innate Sorcery"``); these are
    appended to the prior row before token extraction.
    """
    rows: list[tuple[int, list[str]]] = []
    # ``header_line`` is a 1-indexed line number (matches ``awk NR``); the
    # first row sits on the next line, so start scanning at that index.
    i = header_line  # lines[header_line] == awk NR header_line+1 = first row
    while i < len(lines) and len(rows) < 20:
        line = lines[i].rstrip()
        m = ROW_RE.match(line)
        if m:
            rows.append((int(m.group(1)), [line]))
        else:
            stripped = line.strip()
            if not stripped:
                # Blank line; tables are dense, but a blank typically marks
                # the end of the 20-row table.
                if rows and len(rows) >= 1 and i - header_line > 25 and not stripped:
                    # Just continue scanning a few more lines in case the
                    # blank precedes more wrapped rows; we stop after 20.
                    pass
                # Don't break immediately; the table sometimes contains a
                # cosmetic blank line we want to skip.
            elif rows:
                # Continuation of the last row (no leading level number).
                rows[-1][1].append(stripped)
        i += 1
        # Safety net: stop if we've scanned far past where a 20-row table
        # could plausibly end.
        if i - header_line > 80:
            break
    return [(lvl, " ".join(parts)) for lvl, parts in rows]


def _trailing_tokens(text: str, count: int) -> list[int]:
    """Extract the last ``count`` numeric/dash tokens from ``text``.

    Dashes are normalised to 0.  Raises ``ValueError`` if fewer tokens are
    found than requested.
    """
    tokens = re.findall(r"(?<![A-Za-z0-9])(?:\d+|[—–-])(?![A-Za-z0-9])", text)
    if len(tokens) < count:
        raise ValueError(f"need {count} trailing tokens but found {len(tokens)} in: {text!r}")
    tail = tokens[-count:]
    out: list[int] = []
    for tok in tail:
        if DASH_RE.fullmatch(tok):
            out.append(0)
        else:
            out.append(int(tok))
    return out


def _parse_class(lines: list[str], spec: dict[str, Any]) -> dict[str, Any]:
    rows = _collect_rows(lines, spec["header_line"])
    if len(rows) != 20:
        raise RuntimeError(f"{spec['key']}: expected 20 rows, got {len(rows)}")

    progression: dict[str, dict[str, Any]] = {}
    for level, text in rows:
        # Proficiency bonus is the first +N token after the level.
        m = re.match(r"\s*\d{1,2}\s+\+(\d+)", text)
        if not m:
            raise RuntimeError(f"{spec['key']} L{level}: cannot find proficiency bonus")
        pb = int(m.group(1))
        tail = _trailing_tokens(text, spec["trailing"])

        if spec["kind"] == "warlock":
            cantrips, spells, slot_count, slot_level = tail
            entry: dict[str, Any] = {
                "proficiency_bonus": pb,
                "cantrips_known": cantrips,
                "spells_known": spells,
                "pact_slots": {"count": slot_count, "level": slot_level},
            }
            # Invocations column appears just before the trailing 4 tokens.
            inv_match = re.search(
                r"(\d+|[—–-])\s+\d+\s+\d+\s+\d+\s+\d+\s*$", text
            )
            if inv_match:
                inv = inv_match.group(1)
                entry["invocations"] = 0 if DASH_RE.fullmatch(inv) else int(inv)
        elif spec["kind"] == "full":
            cantrips = tail[0]
            spells = tail[1]
            slots = tail[2:]  # 9 slot levels
            entry = {
                "proficiency_bonus": pb,
                "cantrips_known": cantrips,
                "spells_known": spells,
                "slots": slots,
            }
        elif spec["kind"] == "half":
            spells = tail[0]
            slots5 = tail[1:]  # 5 slot levels
            slots = slots5 + [0, 0, 0, 0]  # pad to 9 for uniform shape
            entry = {
                "proficiency_bonus": pb,
                "cantrips_known": 0,
                "spells_known": spells,
                "slots": slots,
            }
        else:  # pragma: no cover
            raise AssertionError(spec["kind"])
        progression[str(level)] = entry

    return {
        "ability": spec["ability"],
        "casting_style": spec["casting_style"],
        "kind": spec["kind"],
        "progression": progression,
    }


def main() -> int:
    if not SRC.exists():
        print(f"missing source: {SRC}", file=sys.stderr)
        return 1
    lines = _load_lines()
    out: dict[str, Any] = {
        "_doc": (
            "Per-class spell progression parsed from SRD 5.2.1 class-features "
            "tables. Generated by tools/parse_srd_progression.py; do not edit "
            "by hand. ``slots`` is a length-9 list (slot levels 1..9); "
            "warlock uses ``pact_slots: {count, level}`` instead. "
            "``spells_known`` carries the *Spells* column - for ``casting_style`` "
            "= 'prepared' it is the per-day prepared cap, for 'known' it is the "
            "lifetime known cap."
        ),
        "classes": {},
    }
    for spec in CLASS_TABLES:
        out["classes"][spec["key"]] = _parse_class(lines, spec)

    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(out['classes'])} class progressions to {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

