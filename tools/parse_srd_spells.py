#!/usr/bin/env python3
"""Parse spell descriptions out of the SRD 5.2.1 plain-text dump.

The input file (``assets/srd5_2/raw/srd_5_2_1.txt``) is a ``pdftotext -layout``
extraction of the official SRD PDF.  Spell descriptions live in a two-column
layout between the "Spell Descriptions" header and the "Rules Glossary"
header.  Each PDF line in the dump contains the *left* column up to roughly
column 64 and the *right* column from column 64 onward; pages are separated
by footer lines such as ``107   System Reference Document 5.2.1``.

This script:

1.  Linearises the two-column layout, page by page, into a single stream of
    text.
2.  Splits the stream into one block per spell using the ``Level N <School>``
    / ``<School> Cantrip`` heading line that always follows the spell name.
3.  Parses each block into a structured record (level, school, classes,
    casting time, range, components, duration, area of effect, attack/save,
    primary damage, scaling, conditions inflicted, full description).

The parser is conservative: when a field cannot be detected reliably it is
omitted (or set to ``None``) rather than guessed.  The full ``description``
text is always preserved so downstream consumers can fall back to it.

Output is written to ``assets/srd5_2/spells.json`` as a JSON object keyed by
spell ``id`` (snake_case of the name).
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "srd5_2" / "raw" / "srd_5_2_1.txt"
DST = ROOT / "assets" / "srd5_2" / "spells.json"

# Fallback column boundary if per-page detection fails.
COL_SPLIT = 62

SCHOOLS = {
    "abjuration",
    "conjuration",
    "divination",
    "enchantment",
    "evocation",
    "illusion",
    "necromancy",
    "transmutation",
}

CLASSES = {
    "bard",
    "cleric",
    "druid",
    "paladin",
    "ranger",
    "sorcerer",
    "warlock",
    "wizard",
}

ABILITIES = {
    "strength": "str",
    "dexterity": "dex",
    "constitution": "con",
    "intelligence": "int",
    "wisdom": "wis",
    "charisma": "cha",
}

CONDITIONS = {
    "blinded",
    "charmed",
    "deafened",
    "frightened",
    "grappled",
    "incapacitated",
    "invisible",
    "paralyzed",
    "petrified",
    "poisoned",
    "prone",
    "restrained",
    "stunned",
    "unconscious",
    "exhaustion",
}

DAMAGE_TYPES = {
    "acid",
    "bludgeoning",
    "cold",
    "fire",
    "force",
    "lightning",
    "necrotic",
    "piercing",
    "poison",
    "psychic",
    "radiant",
    "slashing",
    "thunder",
}

PAGE_FOOTER_RE = re.compile(r"^\s*\d+\s+System Reference Document\s+5\.2\.1\s*$")
HEADING_RE = re.compile(
    r"^\s*(?:Level\s+(\d+)\s+([A-Za-z]+)|([A-Za-z]+)\s+Cantrip)"
    r"(?:\s*\(([^)]+)\))?\s*$"
)
DICE_RE = re.compile(r"\b(\d+d\d+)\b")
SLOT_BUMP_RE = re.compile(
    r"increases? by\s+(\d+d\d+)\s+for each spell slot level above\s+(\d+)",
    re.IGNORECASE,
)
CANTRIP_LEVELS_RE = re.compile(
    r"levels\s+(\d+)\s*\([^)]+\),\s*(\d+)\s*\([^)]+\),?\s*and\s+(\d+)\s*\([^)]+\)",
    re.IGNORECASE,
)
AREA_RE = re.compile(
    r"(\d+)-foot(?:-radius)?\s+(Cone|Cube|Line|Sphere|Emanation|Cylinder)",
    re.IGNORECASE,
)
RANGE_FEET_RE = re.compile(r"^(\d+)\s+feet$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Stage 1: load + linearise two-column text
# ---------------------------------------------------------------------------

def load_spell_section(path: Path) -> str:
    """Return the spell-descriptions text with two-column layout removed."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()

    # Locate the section bounds.  These headings live in the *left* column
    # so we must check the left half of each line, not the full strip.
    start = end = None
    for idx, line in enumerate(lines):
        left = line[:COL_SPLIT].strip()
        if start is None and left == "Spell Descriptions":
            start = idx + 1
        elif start is not None and left == "Rules Glossary":
            end = idx
            break
    if start is None or end is None:
        raise RuntimeError("Could not locate Spell Descriptions section")

    # Walk page by page, pushing left then right column.
    pages: list[list[str]] = [[]]
    for line in lines[start:end]:
        if PAGE_FOOTER_RE.match(line):
            pages.append([])
            continue
        pages[-1].append(line)

    out: list[str] = []
    for page in pages:
        left, right = _split_two_columns(page)
        out.extend(left)
        out.extend(right)
    text = "\n".join(out)
    # Normalise typographic punctuation so downstream regexes are simpler.
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    return text


def _split_two_columns(page_lines: list[str]) -> tuple[list[str], list[str]]:
    """Split a page's raw lines into ``(left_lines, right_lines)``.

    The pdftotext dump preserves visual layout but the right-column start
    column drifts a few characters between pages (and even between lines on
    the same page) because of variable-width glyph kerning.  We detect the
    most common right-column start position on this page, then split each
    line at the gutter immediately to the left of that column.
    """
    # Candidate right-column start = first character after a gap of >=2 spaces
    # somewhere in the central x-range typical for the SRD's two-column body.
    cands: Counter[int] = Counter()
    for line in page_lines:
        for m in re.finditer(r"  +(\S)", line):
            pos = m.start(1)
            if 55 <= pos <= 75:
                cands[pos] += 1
    if not cands:
        # Single-column page (e.g. tables, big headings); attribute everything
        # to the left bucket so the linearisation still includes it.
        return ([line.rstrip() for line in page_lines], [])

    rcs = cands.most_common(1)[0][0]
    # Allow a small fuzz window around rcs because of glyph drift.
    fuzz = 2

    left_out: list[str] = []
    right_out: list[str] = []
    for raw in page_lines:
        line = raw.rstrip()
        if not line:
            left_out.append("")
            right_out.append("")
            continue

        # Find the right-column start on this line: first index in
        # [rcs-fuzz, rcs+fuzz] that is non-space and is preceded by >=2
        # spaces.  Fall back to a global search if none qualifies.
        split_at = None
        for cand in range(max(0, rcs - fuzz), min(len(line), rcs + fuzz + 1)):
            if cand >= len(line):
                break
            if line[cand] == " ":
                continue
            # Require a >=2-space gap immediately before.
            if cand >= 2 and line[cand - 1] == " " and line[cand - 2] == " ":
                split_at = cand
                break
        if split_at is None:
            # No right-column text on this line: attribute everything to the
            # column we'd expect from where the text actually starts.
            stripped_idx = len(line) - len(line.lstrip())
            if stripped_idx >= rcs - fuzz:
                left_out.append("")
                right_out.append(line)
            else:
                left_out.append(line)
                right_out.append("")
            continue

        # Walk left from split_at, eating gap spaces, to find left-text end.
        i = split_at
        while i > 0 and line[i - 1] == " ":
            i -= 1
        left_out.append(line[:i].rstrip())
        right_out.append(line[split_at:].rstrip())

    return left_out, right_out


# ---------------------------------------------------------------------------
# Stage 2: split linearised text into per-spell blocks
# ---------------------------------------------------------------------------

def split_blocks(text: str) -> list[tuple[str, dict[str, Any], list[str]]]:
    """Yield ``(name, header_info, body_lines)`` tuples for each spell."""
    raw_lines = text.splitlines()
    # Trim each line and drop completely empty ones we don't need (we keep
    # blanks for paragraph detection but normalise whitespace).
    lines = [ln.rstrip() for ln in raw_lines]
    # Join headings whose class list wraps across lines:
    #   "Level 1 Abjuration (Bard, Cleric, Druid, Paladin,"
    #   "Ranger)"
    # collapses into a single logical line.
    lines = _join_wrapped_headings(lines)

    blocks: list[tuple[str, dict[str, Any], list[str]]] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        m = HEADING_RE.match(line)
        if not m:
            i += 1
            continue

        # The spell name is the most recent non-empty line above this one
        # that *isn't* itself a heading or a bullet/keyword line.
        name = None
        j = i - 1
        while j >= 0:
            prev = lines[j].strip()
            if prev:
                name = prev
                break
            j -= 1
        if not name or not _looks_like_spell_name(name):
            i += 1
            continue

        level = int(m.group(1)) if m.group(1) else 0
        school = (m.group(2) or m.group(3) or "").lower()
        classes_raw = m.group(4) or ""
        classes = _parse_classes(classes_raw)
        if school not in SCHOOLS:
            i += 1
            continue

        # Collect body until the next heading line whose preceding non-empty
        # line is also a plausible spell name.
        body: list[str] = []
        k = i + 1
        next_heading = None
        while k < n:
            mm = HEADING_RE.match(lines[k].strip())
            if mm:
                # Find the line above k that is the candidate spell name.
                p = k - 1
                while p > i:
                    prev = lines[p].strip()
                    if prev:
                        if _looks_like_spell_name(prev):
                            next_heading = (k, p)
                        break
                    p -= 1
                if next_heading:
                    break
            k += 1

        end_body = next_heading[1] if next_heading else n
        body = lines[i + 1 : end_body]

        blocks.append(
            (
                name,
                {"level": level, "school": school, "classes": classes},
                body,
            )
        )

        if next_heading:
            i = next_heading[0]
        else:
            break

    return blocks


def _join_wrapped_headings(lines: list[str]) -> list[str]:
    """Stitch ``Level X School (...`` lines whose class list wraps."""
    out: list[str] = []
    i = 0
    n = len(lines)
    open_re = re.compile(r"^\s*(?:Level\s+\d+\s+[A-Za-z]+|[A-Za-z]+\s+Cantrip)\s*\([^)]*$")
    while i < n:
        line = lines[i]
        if open_re.match(line):
            merged = line
            j = i + 1
            while j < n and ")" not in merged:
                nxt = lines[j].strip()
                if nxt:
                    merged = merged.rstrip() + " " + nxt
                j += 1
                if ")" in merged:
                    break
            out.append(merged)
            i = j
        else:
            out.append(line)
            i += 1
    return out


def _looks_like_spell_name(s: str) -> bool:
    """Heuristic: spell names are short Title Case strings."""
    if not s or len(s) > 60:
        return False
    if s.endswith(":") or s.endswith("."):
        return False
    if s.startswith(("- ", "* ", "Using ", "Cantrip Upgrade")):
        return False
    # Must contain at least one letter and start with a capital or digit.
    if not s[0].isupper() and not s[0].isdigit():
        return False
    # Reject lines that look like a sentence (lots of common lowercase words).
    words = s.split()
    if len(words) > 8:
        return False
    # Rule out obvious non-name patterns.
    bad = {"the", "and", "you", "a", "an", "of", "with", "in", "on"}
    if words[0].lower() in bad:
        return False
    return True


def _parse_classes(raw: str) -> list[str]:
    out = []
    for tok in re.split(r"[,/]", raw):
        t = tok.strip().lower().rstrip(".")
        if t in CLASSES:
            out.append(t)
    return out


# ---------------------------------------------------------------------------
# Stage 3: parse one spell block into structured fields
# ---------------------------------------------------------------------------

KEYWORDS = ("Casting Time:", "Range:", "Components:", "Component:", "Duration:")


def parse_block(name: str, header: dict[str, Any], body: list[str]) -> dict[str, Any] | None:
    # Stitch wrapped lines for the keyword section.
    joined = _join_wrapped(body)
    fields: dict[str, str] = {}
    description_start = 0
    for idx, line in enumerate(joined):
        for kw in KEYWORDS:
            if line.startswith(kw):
                key = kw[:-1]
                # Normalise the singular "Component:" used by a few spells.
                if key == "Component":
                    key = "Components"
                fields[key] = line[len(kw) :].strip()
                description_start = idx + 1
                break
        else:
            # First non-keyword line after we've seen Duration => description.
            if "Duration" in fields:
                break
    if not all(k in fields for k in ("Casting Time", "Range", "Components", "Duration")):
        return None

    desc_lines = joined[description_start:]
    description = _clean_description(desc_lines)

    spell: dict[str, Any] = {
        "id": _slug(name),
        "name": name,
        "level": header["level"],
        "school": header["school"],
        "classes": header["classes"],
        "casting_time": _parse_casting_time(fields["Casting Time"]),
        "range": _parse_range(fields["Range"]),
        "components": _parse_components(fields["Components"]),
        "duration": _parse_duration(fields["Duration"]),
    }

    area = _parse_area(description, fields["Range"])
    if area:
        spell["area"] = area

    targeting = _infer_targeting(description, area, spell["range"])
    spell["targeting"] = targeting

    attack = _parse_attack(description)
    if attack:
        spell["attack"] = attack

    damage = _parse_damage(description)
    if damage:
        spell["damage"] = damage

    scaling = _parse_scaling(description, header["level"])
    if scaling:
        spell["scaling"] = scaling

    effects = _parse_effects(description)
    if effects:
        spell["effects"] = effects

    spell["description"] = description
    return spell


def _join_wrapped(lines: Iterable[str]) -> list[str]:
    """Join soft-wrapped lines into logical lines.

    Heuristic: a line continues onto the next line when both are non-empty and
    the next line is not a keyword/heading-style line.  Bullet items (lines
    starting with extra indent) are kept as their own logical lines.

    For keyword-value buffers (Casting Time / Range / Components / Duration)
    we are stricter: continuation only happens when the previous fragment
    ends with a ``-`` (PDF hyphenation) or ``,`` (clause continuation), or
    when the next line starts with a lowercase letter.  This prevents
    description prose from being glued onto a one-word Duration value.
    """
    out: list[str] = []
    buf = ""
    buf_is_keyword = False
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            if buf:
                out.append(buf.strip())
                buf = ""
                buf_is_keyword = False
            out.append("")
            continue

        starts_keyword = any(stripped.startswith(k) for k in KEYWORDS)
        starts_section = stripped.startswith(("Using a Higher", "Cantrip Upgrade"))

        if starts_keyword or starts_section:
            if buf:
                out.append(buf.strip())
            buf = stripped
            buf_is_keyword = starts_keyword
            continue

        if buf:
            if buf_is_keyword:
                ends_continuation = buf.endswith("-") or buf.endswith(",")
                starts_lower = stripped[:1].islower()
                if not (ends_continuation or starts_lower):
                    out.append(buf.strip())
                    buf = stripped
                    buf_is_keyword = False
                    continue
            if buf.endswith("-") and not buf.endswith(" -"):
                buf = buf[:-1] + stripped
            else:
                buf = buf + " " + stripped
        else:
            buf = stripped
            buf_is_keyword = False

    if buf:
        out.append(buf.strip())
    return out


def _clean_description(lines: Iterable[str]) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if not line.strip():
            if current:
                paragraphs.append(" ".join(current).strip())
                current = []
        else:
            current.append(line.strip())
    if current:
        paragraphs.append(" ".join(current).strip())
    text = "\n\n".join(p for p in paragraphs if p)
    # Collapse runs of whitespace.
    text = re.sub(r"[ \t]+", " ", text)
    return text


def _slug(name: str) -> str:
    s = name.lower()
    s = re.sub(r"['']", "", s)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


# ---- field parsers --------------------------------------------------------

def _parse_casting_time(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    out: dict[str, Any] = {"raw": raw}
    ritual = False
    if "Ritual" in raw:
        ritual = True
        raw = re.sub(r"\s*or\s*Ritual", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*Ritual", "", raw, flags=re.IGNORECASE)
    raw = raw.strip().rstrip(".")
    lower = raw.lower()
    if lower == "action":
        out["unit"] = "action"
    elif lower == "bonus action":
        out["unit"] = "bonus_action"
    elif lower == "reaction":
        out["unit"] = "reaction"
    else:
        m = re.match(r"(\d+)\s+(minute|minutes|hour|hours)", lower)
        if m:
            unit = m.group(2).rstrip("s")
            out["unit"] = unit
            out["amount"] = int(m.group(1))
        elif lower.startswith("reaction"):
            out["unit"] = "reaction"
            out["trigger"] = raw.split(",", 1)[1].strip() if "," in raw else None
    out["ritual"] = ritual
    return out


def _parse_range(raw: str) -> Any:
    s = raw.strip().rstrip(".")
    low = s.lower()
    if low == "self":
        return "self"
    if low == "touch":
        return "touch"
    if low == "sight":
        return "sight"
    if low == "unlimited":
        return "unlimited"
    if low == "special":
        return "special"
    m = RANGE_FEET_RE.match(s)
    if m:
        return int(m.group(1))
    m = re.match(r"(\d+)\s+miles?", s, re.IGNORECASE)
    if m:
        return {"miles": int(m.group(1))}
    # "Self (30-foot Cone)" etc.
    if low.startswith("self"):
        return "self"
    return s  # fall back to raw text


def _parse_components(raw: str) -> dict[str, Any]:
    out: dict[str, Any] = {"v": False, "s": False, "m": False}
    s = raw.strip().rstrip(".")
    # Material parenthetical, if any.
    mat = None
    mm = re.search(r"M\s*\(([^)]+)\)", s)
    if mm:
        mat = mm.group(1).strip()
        s = s[: mm.start()] + s[mm.end() :]
    parts = [p.strip().rstrip(".") for p in re.split(r",", s) if p.strip()]
    for p in parts:
        if p == "V":
            out["v"] = True
        elif p == "S":
            out["s"] = True
        elif p == "M":
            out["m"] = True
    if mat is not None:
        out["m"] = True
        out["material"] = mat
    return out


def _parse_duration(raw: str) -> dict[str, Any]:
    s = raw.strip().rstrip(".")
    low = s.lower()
    out: dict[str, Any] = {"raw": s, "concentration": False}
    if low == "instantaneous":
        out["kind"] = "instantaneous"
        return out
    if low.startswith("until dispelled"):
        out["kind"] = "until_dispelled"
        return out
    if low == "special":
        out["kind"] = "special"
        return out
    conc = False
    body = s
    if low.startswith("concentration"):
        conc = True
        body = re.sub(r"^concentration[,\s]+up to\s+", "", s, flags=re.IGNORECASE)
    out["concentration"] = conc
    m = re.match(r"(\d+)\s+(round|rounds|minute|minutes|hour|hours|day|days)", body, re.IGNORECASE)
    if m:
        unit = m.group(2).lower().rstrip("s")
        out["kind"] = "timed"
        out["amount"] = int(m.group(1))
        out["unit"] = unit
    else:
        out["kind"] = "other"
    return out


def _parse_area(description: str, range_raw: str) -> dict[str, Any] | None:
    # Prefer area declared on the Range line ("Self (30-foot Cone)").
    m = AREA_RE.search(range_raw)
    anchor = "self" if m else "point"
    if not m:
        m = AREA_RE.search(description)
        anchor = "point"
    if not m:
        return None
    return {
        "shape": m.group(2).lower(),
        "size_ft": int(m.group(1)),
        "anchor": anchor,
    }


def _infer_targeting(description: str, area: dict[str, Any] | None, rng: Any) -> dict[str, Any]:
    if rng == "self" and not area:
        return {"type": "self"}
    if area:
        return {"type": "area", "shape": area["shape"]}
    low = description.lower()
    if "make a melee spell attack" in low:
        return {"type": "single_creature"}
    if "make a ranged spell attack" in low:
        return {"type": "single_creature"}
    if "creature you can see" in low or "one creature" in low or "a creature within range" in low:
        return {"type": "single_creature"}
    if "choose up to" in low or re.search(r"up to (\w+) creatures", low):
        return {"type": "multiple_creatures"}
    if "object" in low and "creature" not in low.split(".")[0]:
        return {"type": "object"}
    return {"type": "unspecified"}


def _parse_attack(description: str) -> dict[str, Any] | None:
    low = description.lower()
    if "make a ranged spell attack" in low:
        return {"type": "spell_attack", "range": "ranged"}
    if "make a melee spell attack" in low:
        return {"type": "spell_attack", "range": "melee"}
    m = re.search(
        r"(strength|dexterity|constitution|intelligence|wisdom|charisma)\s+saving throw",
        low,
    )
    if m:
        return {"type": "saving_throw", "ability": ABILITIES[m.group(1)]}
    return None


def _parse_damage(description: str) -> dict[str, Any] | None:
    # Look for the first "<dice> ... <type> damage" pattern.
    pattern = re.compile(
        r"(\d+d\d+(?:\s*\+\s*\d+)?)\s+(?:[A-Za-z\- ]{0,40}?)?(" + "|".join(DAMAGE_TYPES) + r")\s+damage",
        re.IGNORECASE,
    )
    m = pattern.search(description)
    if not m:
        return None
    return {"dice": m.group(1).replace(" ", ""), "type": m.group(2).lower()}


def _parse_scaling(description: str, level: int) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    m = SLOT_BUMP_RE.search(description)
    if m:
        out["higher_level"] = {
            "dice_per_slot_above": m.group(1),
            "base_level": int(m.group(2)),
        }
    if level == 0:
        m = CANTRIP_LEVELS_RE.search(description)
        if m:
            out["cantrip_levels"] = [int(m.group(i)) for i in (1, 2, 3)]
    return out or None


def _parse_effects(description: str) -> list[dict[str, Any]] | None:
    effects: list[dict[str, Any]] = []
    low = description.lower()
    for cond in CONDITIONS:
        if re.search(rf"\b{cond}\b condition", low):
            effects.append({"type": "condition", "condition": cond})
    if "can't regain hit points" in low or "cannot regain hit points" in low:
        effects.append({"type": "prevent_healing"})
    if "speed" in low and re.search(r"speed (?:is reduced|drops|becomes 0)", low):
        effects.append({"type": "speed_modifier"})
    return effects or None


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    if not SRC.exists():
        print(f"missing source: {SRC}", file=sys.stderr)
        return 1
    text = load_spell_section(SRC)
    blocks = split_blocks(text)
    spells: dict[str, dict[str, Any]] = {}
    skipped: list[str] = []
    for name, header, body in blocks:
        spell = parse_block(name, header, body)
        if spell is None:
            skipped.append(name)
            continue
        sid = spell["id"]
        if sid in spells:
            # Disambiguate (shouldn't really happen for SRD spells).
            sid = f"{sid}_{len(spells)}"
            spell["id"] = sid
        spells[sid] = spell

    DST.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_doc": (
            "Structured spell catalog parsed from SRD 5.2.1. Generated by "
            "tools/parse_srd_spells.py; do not edit by hand. See the script's "
            "module docstring for the field schema."
        ),
        "_count": len(spells),
        "spells": spells,
    }
    DST.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"wrote {len(spells)} spells to {DST}")
    if skipped:
        print(f"skipped {len(skipped)} blocks (incomplete keyword headers): "
              + ", ".join(skipped[:10]) + ("..." if len(skipped) > 10 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

