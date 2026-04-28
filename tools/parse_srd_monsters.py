#!/usr/bin/env python3
"""Parse monster stat blocks out of the SRD 5.2.1 plain-text dump.

Input
-----
``assets/srd5_2/raw/srd_5_2_1.txt`` is a ``pdftotext -layout`` extraction
of the official SRD PDF. Monster stat blocks live in a two-column layout
between the ``Monsters A-Z`` heading and the end of the document. Pages
are separated by footer lines such as ``258   System Reference Document
5.2.1`` and a literal form-feed (``\\f``) is emitted at every page break.

Output
------
``assets/srd5_2/monsters.json`` is a dict keyed by monster ``key``
(snake_case of the name). Each entry conforms (as closely as text
parsing allows) to :class:`ai_dm.game.monster_state.StatBlock` so the
catalog can be hydrated with ``StatBlock.model_validate(...)``.

Approach
--------
1.  Linearise the two columns of every page into a single text stream
    (top-of-left-column, then top-of-right-column for each page) so the
    on-the-page reading order is preserved within one stat block. This
    matters because most stat blocks span both columns of a page.
2.  Split the stream into per-monster blocks by detecting the
    ``Size [Type, ]Alignment`` line that immediately follows every
    monster name. The line right above it is the monster name (the SRD
    repeats the name as a section header just above the stat block, so
    we deduplicate).
3.  Walk each block extracting AC / HP / Speed / abilities / saves /
    defences / senses / languages / CR / XP / PB, then split the
    remainder on the section headings (Traits / Actions / Bonus
    Actions / Reactions / Legendary Actions) and parse each entry into
    a structured payload (with an attack roll / save block when the
    description's prose makes it unambiguous).

The parser is deliberately conservative: when a sub-field cannot be
detected reliably it is omitted (or its raw text kept) rather than
guessed. The full description text of every entry is always kept so
downstream consumers can fall back to it.
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
DST = ROOT / "assets" / "srd5_2" / "monsters.json"

PAGE_FOOTER_RE = re.compile(r"^\s*\d+\s+System Reference Document\s+5\.2\.1\s*$")

SIZES = ("Tiny", "Small", "Medium", "Large", "Huge", "Gargantuan")
CREATURE_TYPES = {
    "aberration", "beast", "celestial", "construct", "dragon", "elemental",
    "fey", "fiend", "giant", "humanoid", "monstrosity", "ooze", "plant",
    "undead",
}

# Either a single size, or "X or Y" (e.g. "Medium or Small").
SIZE_RE = "|".join(SIZES)
SIZE_LINE_RE = re.compile(
    rf"^\s*((?:{SIZE_RE})(?:\s+or\s+(?:{SIZE_RE}))?)\s+"
    r"([A-Z][a-z]+)"          # type word (Aberration, Beast, ...)
    r"(?:\s+\(([^)]+)\))?"    # optional subtype "(Demon)"
    r",\s*([A-Za-z][A-Za-z ]*?)\s*$"
)

ABILITY_RE = re.compile(
    r"\b(Str|Dex|Con|Int|Wis|Cha)\s+(\d+)\s+([+-]?\d+)\s+([+-]?\d+)\b"
)
ABILITY_KEYS = {
    "Str": "str", "Dex": "dex", "Con": "con",
    "Int": "int", "Wis": "wis", "Cha": "cha",
}

AC_RE = re.compile(r"^\s*AC\s+(\d+)")
HP_RE = re.compile(r"^\s*HP\s+(\d+)\s*\(([^)]+)\)")
SPEED_RE = re.compile(r"^\s*Speed\s+(.+)$")
SPEED_PART_RE = re.compile(r"(?:(Fly|Swim|Climb|Burrow)\s+)?(\d+)\s*ft\.?(\s*\(hover\))?", re.IGNORECASE)

CR_RE = re.compile(
    r"^\s*CR\s+([0-9]+(?:/[0-9]+)?)"
    r"(?:\s*\(\s*XP\s+([0-9,]+)(?:[^;]*)?(?:;\s*PB\s*\+(\d+))?\s*\))?"
)

DAMAGE_TYPES = {
    "acid", "bludgeoning", "cold", "fire", "force", "lightning", "necrotic",
    "piercing", "poison", "psychic", "radiant", "slashing", "thunder",
}
ABILITIES_LONG = {
    "strength": "str", "dexterity": "dex", "constitution": "con",
    "intelligence": "int", "wisdom": "wis", "charisma": "cha",
}

SECTION_HEADINGS = (
    "Traits", "Actions", "Bonus Actions", "Reactions", "Legendary Actions",
    "Utility Spells", "Spellcasting",
)
SECTION_KEYS = {
    "Traits": "traits",
    "Actions": "actions",
    "Bonus Actions": "bonus_actions",
    "Reactions": "reactions",
    "Legendary Actions": "legendary_actions",
}

# Field-prefix keywords on the header block (above the first section).
HEADER_PREFIXES = (
    "Saving Throws", "Skills", "Vulnerabilities", "Resistances",
    "Immunities", "Gear", "Senses", "Languages", "CR",
)

# Recognised attack-roll / save lead-ins in entry prose.
MELEE_ATTACK_RE = re.compile(r"Melee Attack Roll:\s*([+-]?\d+)", re.IGNORECASE)
RANGED_ATTACK_RE = re.compile(r"Ranged Attack Roll:\s*([+-]?\d+)", re.IGNORECASE)
REACH_RE = re.compile(r"reach\s+(\d+)\s*ft", re.IGNORECASE)
RANGE_RE = re.compile(r"range\s+(\d+)(?:/(\d+))?\s*ft", re.IGNORECASE)
HIT_DAMAGE_RE = re.compile(
    r"Hit:\s*\d+\s*\(([^)]+)\)\s+([A-Za-z]+)\s+damage"
    r"(?:\s+plus\s+\d+\s*\(([^)]+)\)\s+([A-Za-z]+)\s+damage)?",
    re.IGNORECASE,
)
SAVE_RE = re.compile(
    r"\b(Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma)\s+Saving Throw:\s*DC\s*(\d+)",
    re.IGNORECASE,
)
RECHARGE_RE = re.compile(r"\(\s*Recharge\s+([0-9]+(?:[\u2013\u2014\-][0-9]+)?)\s*\)", re.IGNORECASE)
PER_DAY_RE = re.compile(r"\(\s*(\d+)\s*/\s*Day\b[^)]*\)", re.IGNORECASE)
HALF_ON_SAVE_RE = re.compile(r"Success:\s*Half damage", re.IGNORECASE)


# --------------------------------------------------------------------- #
# Stage 1: load + linearise
# --------------------------------------------------------------------- #

def load_monster_section(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()

    # Find the start of the "Monsters A-Z" section.
    start = None
    for idx, line in enumerate(lines):
        # The heading is centered in the left column, e.g. "Monsters A-Z".
        s = line.strip()
        if s.startswith("Monsters A") and "Z" in s:
            start = idx + 1
            break
    if start is None:
        raise RuntimeError("Could not locate 'Monsters A-Z' section")

    # Walk page by page, pushing left then right column.
    pages: list[list[str]] = [[]]
    for line in lines[start:]:
        # Form-feed marks page break in pdftotext output.
        if "\f" in line:
            for piece in line.split("\f"):
                if piece.strip():
                    pages[-1].append(piece)
                pages.append([])
            pages.pop()  # last appended page is empty until next iteration
            pages.append([])
            continue
        if PAGE_FOOTER_RE.match(line):
            pages.append([])
            continue
        pages[-1].append(line)

    out: list[str] = []
    for page in pages:
        left, right = _split_two_columns(page)
        out.extend(left)
        out.append("")  # paragraph break between columns
        out.extend(right)
        out.append("")
    text = "\n".join(out)
    text = unicodedata.normalize("NFKC", text)
    text = (text
            .replace("\u2019", "'").replace("\u2018", "'")
            .replace("\u201c", '"').replace("\u201d", '"')
            .replace("\u2013", "-").replace("\u2014", "-")
            .replace("\u2212", "-")  # Unicode minus
            .replace("\t", "    "))
    return text


def _split_two_columns(page_lines: list[str]) -> tuple[list[str], list[str]]:
    """Return (left, right) lines for one page using a per-page gutter detection."""
    cands: Counter[int] = Counter()
    for line in page_lines:
        for m in re.finditer(r"  +(\S)", line):
            pos = m.start(1)
            if 50 <= pos <= 80:
                cands[pos] += 1
    if not cands:
        return ([line.rstrip() for line in page_lines], [])

    rcs = cands.most_common(1)[0][0]
    fuzz = 4

    left_out: list[str] = []
    right_out: list[str] = []
    for raw in page_lines:
        line = raw.rstrip()
        if not line:
            left_out.append("")
            right_out.append("")
            continue

        split_at = None
        # 1) Try near the page-wide gutter rcs.
        for cand in range(max(0, rcs - fuzz), min(len(line), rcs + fuzz + 1)):
            if cand >= len(line) or line[cand] == " ":
                continue
            if cand >= 2 and line[cand - 1] == " " and line[cand - 2] == " ":
                split_at = cand
                break
        # 2) Otherwise, find any wide gap (>=3 spaces) somewhere in the
        #    central gutter band -- useful when the right-column content
        #    drifts far right of the page's typical rcs.
        if split_at is None:
            for m in re.finditer(r"   +(\S)", line):
                pos = m.start(1)
                if 40 <= pos <= 100:
                    split_at = pos
                    break
        if split_at is None:
            stripped_idx = len(line) - len(line.lstrip())
            if stripped_idx >= rcs - fuzz:
                left_out.append("")
                right_out.append(line)
            else:
                left_out.append(line)
                right_out.append("")
            continue

        i = split_at
        while i > 0 and line[i - 1] == " ":
            i -= 1
        left_out.append(line[:i].rstrip())
        right_out.append(line[split_at:].rstrip())
    return left_out, right_out


# --------------------------------------------------------------------- #
# Stage 2: split into per-monster blocks
# --------------------------------------------------------------------- #

def split_blocks(text: str) -> list[tuple[str, list[str]]]:
    lines = [ln.rstrip() for ln in text.splitlines()]

    # Find every Size/Type/Alignment line.
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = SIZE_LINE_RE.match(line)
        if not m:
            continue
        # The type word must be a known SRD creature type.
        if m.group(2).lower() not in CREATURE_TYPES:
            continue
        # Resolve the monster name: nearest non-empty preceding line.
        name = None
        j = i - 1
        while j >= 0:
            prev = lines[j].strip()
            if prev:
                # Avoid grabbing the previous monster's tail prose: the
                # line right above the size/type line should look like a
                # short Title Case name, not a sentence.
                if _looks_like_monster_name(prev):
                    name = prev
                break
            j -= 1
        if not name:
            continue
        starts.append((i, name))

    blocks: list[tuple[str, list[str]]] = []
    for idx, (start, name) in enumerate(starts):
        end = starts[idx + 1][0] - 1 if idx + 1 < len(starts) else len(lines)
        # Scan back from `end` past the (often duplicated) next-monster
        # name line so it stays with the next block.
        while end > start and not lines[end - 1].strip():
            end -= 1
        if end > start and idx + 1 < len(starts):
            # Drop trailing line if it is exactly the next monster name.
            next_name = starts[idx + 1][1]
            if lines[end - 1].strip() == next_name:
                end -= 1
        body = lines[start:end]  # body[0] is the size/type/alignment line
        blocks.append((name, body))
    return blocks


def _looks_like_monster_name(s: str) -> bool:
    if not s or len(s) > 60:
        return False
    if s.endswith((".", ":", ",", ";", ")")):
        return False
    if any(s.lower().startswith(p.lower() + " ") or s.lower() == p.lower()
           for p in HEADER_PREFIXES):
        return False
    if s.lower().startswith(("hit:", "failure:", "success:", "trigger:",
                              "response:", "first failure", "second failure",
                              "while ", "if ", "the ", "this ")):
        return False
    if not s[0].isupper():
        return False
    # Names are usually 1-5 words.
    words = s.split()
    if len(words) > 6:
        return False
    return True


# --------------------------------------------------------------------- #
# Stage 3: parse one block
# --------------------------------------------------------------------- #

def parse_block(name: str, body: list[str]) -> dict[str, Any] | None:
    if not body:
        return None
    header_match = SIZE_LINE_RE.match(body[0])
    if not header_match:
        return None
    size = header_match.group(1)
    ctype = header_match.group(2).lower()
    subtype = header_match.group(3)
    alignment = header_match.group(4).strip()

    out: dict[str, Any] = {
        "key": _slug(name),
        "name": name,
        "size": size,
        "type": ctype,
        "alignment": alignment,
    }
    if subtype:
        out["subtype"] = subtype

    # ------------------------------------------------------------------
    # Pre-pass: split header (vitals + defence keywords) from sections.
    # ------------------------------------------------------------------
    # Find the line index where the first section heading appears.
    section_start = len(body)
    for idx, line in enumerate(body[1:], start=1):
        s = line.strip()
        if s in SECTION_HEADINGS:
            section_start = idx
            break

    header_lines = body[1:section_start]
    section_lines = body[section_start:]

    # Vitals + abilities. Restrict to lines from the AC line onward so
    # stale ability/stat fragments leaking in from a previous monster's
    # right-column tail (which can land *above* this monster's AC line in
    # the linearised stream) are ignored.
    ac_idx = next((i for i, ln in enumerate(header_lines) if AC_RE.match(ln)), 0)
    vitals = header_lines[ac_idx:]

    for line in vitals:
        m = AC_RE.match(line)
        if m:
            out["ac"] = int(m.group(1))
            break
    for line in vitals:
        m = HP_RE.match(line)
        if m:
            out["hp"] = int(m.group(1))
            out["hit_dice"] = m.group(2).replace(" ", "")
            break
    for line in vitals:
        m = SPEED_RE.match(line)
        if m:
            speeds, walk = _parse_speed(m.group(1))
            out["speeds"] = speeds
            out["speed"] = walk
            break

    # Abilities + saves: a monster's ability scores are sometimes split
    # by the column linearisation so that one row (commonly Cha) ends up
    # below the section heading. We scan the full body from the AC line
    # onward, but stop at the next monster's size/type line if any leaked
    # in.
    abilities: dict[str, int] = {}
    saves: dict[str, int] = {}
    scan_lines: list[str] = []
    for ln in body[1:][ac_idx:]:
        if SIZE_LINE_RE.match(ln) and ln.strip() != body[0].strip():
            break
        scan_lines.append(ln)
    joined_for_abilities = "\n".join(scan_lines)
    for m in ABILITY_RE.finditer(joined_for_abilities):
        ab = ABILITY_KEYS[m.group(1)]
        if ab in abilities:
            continue
        score = int(m.group(2))
        mod = int(m.group(3))
        save = int(m.group(4))
        abilities[ab] = score
        # Save proficiency: save bonus differs from raw modifier.
        if save != mod:
            saves[ab] = save
    if abilities:
        out["abilities"] = abilities
    if saves:
        out["saving_throws"] = saves

    # Prefix-based fields (Skills, Resistances, ..., CR).
    joined = _join_wrapped_header(header_lines)
    for line in joined:
        if line.startswith("Skills "):
            out["skills"] = _parse_kv_pairs(line[len("Skills "):])
        elif line.startswith("Saving Throws "):
            # Already captured above from ability rows; only override when
            # the row form was unparseable.
            extra = _parse_kv_pairs(line[len("Saving Throws "):])
            if extra and "saving_throws" not in out:
                out["saving_throws"] = extra
        elif line.startswith("Vulnerabilities "):
            out["vulnerabilities"] = _parse_damage_list(line[len("Vulnerabilities "):])
        elif line.startswith("Resistances "):
            out["resistances"] = _parse_damage_list(line[len("Resistances "):])
        elif line.startswith("Immunities "):
            dmg, cond = _parse_immunities(line[len("Immunities "):])
            if dmg:
                out["immunities"] = dmg
            if cond:
                out["condition_immunities"] = cond
        elif line.startswith("Gear "):
            out["gear"] = [g.strip() for g in line[len("Gear "):].split(",") if g.strip()]
        elif line.startswith("Senses "):
            out["senses"] = _parse_senses(line[len("Senses "):])
        elif line.startswith("Languages "):
            out["languages"] = _parse_languages(line[len("Languages "):])
        elif line.startswith("CR "):
            cr_block = _parse_cr(line)
            if cr_block:
                out.update(cr_block)

    # ------------------------------------------------------------------
    # Sections.
    # ------------------------------------------------------------------
    sections = _split_sections(section_lines)
    for heading, raw_entries in sections.items():
        key = SECTION_KEYS.get(heading)
        entries = _parse_entries(raw_entries)
        if heading == "Legendary Actions":
            out["legendary_actions"], la_per_round = _parse_legendary_entries(entries)
            if la_per_round is not None:
                out["legendary_actions_per_round"] = la_per_round
        elif heading == "Reactions":
            out["reactions"] = [_to_reaction(e) for e in entries]
        elif heading in ("Actions", "Bonus Actions"):
            actions = [_to_action(e, bonus=(heading == "Bonus Actions"))
                       for e in entries]
            out.setdefault("actions", []).extend(actions)
        elif heading == "Traits":
            out["traits"] = [_to_trait(e) for e in entries]
        elif heading in ("Spellcasting", "Utility Spells"):
            # Treat as a trait so the description is preserved.
            spell_traits = [_to_trait(e) for e in entries]
            out.setdefault("traits", []).extend(spell_traits)

    return out


# --------------------------------------------------------------------- #
# Header helpers
# --------------------------------------------------------------------- #

def _parse_speed(raw: str) -> tuple[dict[str, int], int]:
    speeds: dict[str, int] = {}
    walk = 0
    for part in raw.split(","):
        part = part.strip().rstrip(".")
        if not part:
            continue
        m = SPEED_PART_RE.search(part)
        if not m:
            continue
        kind = (m.group(1) or "walk").lower()
        value = int(m.group(2))
        speeds[kind] = value
        if kind == "walk":
            walk = value
    return speeds, walk or next(iter(speeds.values()), 30)


def _join_wrapped_header(lines: Iterable[str]) -> list[str]:
    """Join soft-wrapped header lines (Skills/.../CR) into one logical line each."""
    out: list[str] = []
    buf = ""
    for raw in lines:
        s = raw.strip()
        if not s:
            if buf:
                out.append(buf)
                buf = ""
            continue
        starts_field = any(s.startswith(p + " ") or s == p for p in HEADER_PREFIXES) \
            or s.startswith("CR ")
        if starts_field:
            if buf:
                out.append(buf)
            buf = s
        else:
            if buf:
                buf += " " + s
            else:
                # Not a header field (e.g. AC/HP/Speed line we don't need
                # to keep here, or stray ability row); ignore.
                continue
    if buf:
        out.append(buf)
    return out


def _parse_kv_pairs(raw: str) -> dict[str, int]:
    """Parse 'Perception +5, Stealth +3' → {'perception': 5, 'stealth': 3}."""
    out: dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip().rstrip(".")
        m = re.match(r"([A-Za-z][A-Za-z ]+?)\s+([+-]?\d+)$", part)
        if not m:
            continue
        out[m.group(1).strip().lower()] = int(m.group(2))
    return out


def _parse_damage_list(raw: str) -> list[str]:
    out: list[str] = []
    raw = raw.rstrip(".")
    for part in re.split(r"[,;]", raw):
        p = part.strip()
        low = p.lower()
        if low in DAMAGE_TYPES:
            out.append(low)
        elif p:
            out.append(p)
    return out


CONDITIONS = {
    "blinded", "charmed", "deafened", "exhaustion", "frightened", "grappled",
    "incapacitated", "invisible", "paralyzed", "petrified", "poisoned",
    "prone", "restrained", "stunned", "unconscious",
}


def _parse_immunities(raw: str) -> tuple[list[str], list[str]]:
    """Split 'Fire, Poison; Charmed, Poisoned' → (damage, condition)."""
    raw = raw.rstrip(".")
    if ";" in raw:
        dmg_raw, cond_raw = raw.split(";", 1)
    else:
        # Heuristic: items that look like conditions go in cond bucket.
        toks = [t.strip() for t in raw.split(",") if t.strip()]
        dmg, cond = [], []
        for t in toks:
            (cond if t.lower() in CONDITIONS else dmg).append(t)
        return ([d.lower() if d.lower() in DAMAGE_TYPES else d for d in dmg],
                [c.lower() for c in cond])
    dmg = _parse_damage_list(dmg_raw)
    cond = [c.strip().lower() for c in cond_raw.split(",") if c.strip()]
    return dmg, cond


def _parse_senses(raw: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    raw = raw.rstrip(".")
    parts = [p.strip() for p in re.split(r"[,;]", raw) if p.strip()]
    for p in parts:
        m = re.match(r"(Blindsight|Darkvision|Tremorsense|Truesight)\s+(\d+)\s*ft", p, re.IGNORECASE)
        if m:
            out[m.group(1).lower()] = int(m.group(2))
            continue
        m = re.match(r"Passive Perception\s+(\d+)", p, re.IGNORECASE)
        if m:
            out["passive_perception"] = int(m.group(1))
            continue
    return out


def _parse_languages(raw: str) -> list[str]:
    raw = raw.rstrip(".")
    if raw.strip().lower() == "none":
        return []
    return [p.strip() for p in re.split(r"[,;]", raw) if p.strip()]


def _parse_cr(line: str) -> dict[str, Any] | None:
    m = CR_RE.match(line)
    if not m:
        return None
    cr_raw = m.group(1)
    cr_val: float | int
    if "/" in cr_raw:
        num, den = cr_raw.split("/")
        cr_val = float(num) / float(den)
    else:
        cr_val = int(cr_raw)
    out: dict[str, Any] = {"challenge_rating": cr_val}
    if m.group(2):
        out["xp"] = int(m.group(2).replace(",", ""))
    if m.group(3):
        out["proficiency_bonus"] = int(m.group(3))
    return out


# --------------------------------------------------------------------- #
# Section/entry parsing
# --------------------------------------------------------------------- #

def _split_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    buf: list[str] = []
    for line in lines:
        s = line.strip()
        if s in SECTION_HEADINGS:
            if current is not None:
                sections[current] = buf
            current = s
            buf = []
            continue
        if current is None:
            # Lines before the first heading are ignored.
            continue
        buf.append(line)
    if current is not None:
        sections[current] = buf
    return sections


# An entry begins with "<Bold Name>. " (sometimes with parenthetical
# usage info, e.g. "Petrifying Gaze (Recharge 4-6).", "Legendary
# Resistance (3/Day, or 4/Day in Lair).").  We allow commas/colons here
# because those can appear inside the parenthetical qualifier.
ENTRY_HEAD_RE = re.compile(
    r"^([A-Z][A-Za-z',:; ()/0-9-]{1,120}?)\s*\.\s+([A-Z].*)$"
)

# When entries within a section are not separated by blank lines, we have
# to find entry heads INSIDE a joined paragraph too. An interior entry
# head looks like ". <Title-Case Name (...)?>. <Capital>" where the name
# is 1..5 short tokens, each starting with a capital letter or being a
# parenthetical (Recharge 5-6) / (3/Day) qualifier. We require the
# preceding sentence to end with ". " or ".)" so we don't split at random
# capitalized words mid-sentence.
INTERIOR_HEAD_RE = re.compile(
    r"(?<=[.!?\)])\s+"
    r"(?P<name>[A-Z][A-Za-z'/0-9-]+"
    r"(?:\s+(?:[A-Z][A-Za-z'/0-9-]*|\([^)]+\)|of|the|and|or|in))*"
    r")\.\s+(?=[A-Z])"
)


def _split_paragraph_on_heads(para: str) -> list[str]:
    """Split a joined paragraph into per-entry strings on interior heads.

    Each returned string still starts with the entry head so the caller's
    ENTRY_HEAD_RE will pick it up.
    """
    # Always keep the original first chunk; iterate left-to-right, only
    # splitting where the candidate "head" is plausibly short (<=5 words)
    # and isn't a sentence fragment we should keep glued.
    out: list[str] = []
    last = 0
    for m in INTERIOR_HEAD_RE.finditer(para):
        name = m.group("name").strip()
        # Drop parentheticals from the word count.
        cleaned = re.sub(r"\s*\([^)]*\)", "", name).strip()
        words = cleaned.split()
        if not (1 <= len(words) <= 5):
            continue
        # Reject obvious in-sentence proper nouns by keeping a small
        # blacklist of common single-word continuations.
        if len(words) == 1 and words[0].lower() in {
            "the", "this", "that", "these", "those", "it", "its",
            "first", "second", "failure", "success", "trigger", "response",
            "while", "if", "until", "after", "before", "when", "as", "on",
            "instead",
        }:
            continue
        # Don't split inside parentheses.
        opens = para[:m.start()].count("(") - para[:m.start()].count(")")
        if opens > 0:
            continue
        out.append(para[last:m.start()].strip())
        last = m.start() + len(m.group(0)) - len(m.group(0).lstrip())
        # Move `last` back to the start of `name` so the next chunk
        # begins with the head.
        last = m.start() + (len(m.group(0)) - len(m.group(0).lstrip()))
        # Actually compute the absolute start of the name token:
        name_start = m.start() + (m.group(0).find(name))
        last = name_start
    out.append(para[last:].strip())
    return [s for s in out if s]


# Lines from neighbouring columns can leak ability-row / "MOD SAVE"
# fragments into a stat block's section content. We strip them out
# before grouping section lines into entries.
NOISE_LINE_RE = re.compile(
    r"^\s*(?:MOD\s+SAVE\b|(?:Str|Dex|Con|Int|Wis|Cha)\s+-?\d+\b)"
)


def _is_damage_continuation(name: str) -> bool:
    """A 'Piercing damage' / 'Slashing damage' style false entry head."""
    parts = name.lower().split()
    return (
        len(parts) >= 2
        and parts[0] in DAMAGE_TYPES
        and parts[-1] in {"damage", "damage."}
    )


def _parse_entries(lines: list[str]) -> list[dict[str, str]]:
    """Group lines into entries with {name, description}."""
    # First, join soft wraps within a paragraph: a blank line separates
    # paragraphs, otherwise lines belong to the current paragraph.
    paragraphs: list[str] = []
    cur: list[str] = []
    for raw in lines:
        s = raw.strip()
        if not s or NOISE_LINE_RE.match(s):
            if cur:
                paragraphs.append(" ".join(cur))
                cur = []
            continue
        cur.append(s)
    if cur:
        paragraphs.append(" ".join(cur))

    # Now expand each paragraph into one-or-more sub-paragraphs by
    # splitting on interior entry heads.
    expanded: list[str] = []
    for para in paragraphs:
        expanded.extend(_split_paragraph_on_heads(para))

    entries: list[dict[str, str]] = []
    for para in expanded:
        m = ENTRY_HEAD_RE.match(para)
        if m and not _is_damage_continuation(m.group(1)):
            entries.append({"name": m.group(1).strip(), "description": m.group(2).strip()})
        elif entries:
            # Continuation prose for the previous entry.
            entries[-1]["description"] = (entries[-1]["description"] + " " + para).strip()
        # else: stray prose with no leading entry — discard.
    # Compress runs of whitespace and stitch hyphenated word breaks.
    for e in entries:
        d = re.sub(r"-\s+(?=[a-z])", "", e["description"])  # "sur- rounded" -> "surrounded"
        e["description"] = re.sub(r"\s+", " ", d).strip()
    return entries


def _entry_key(name: str) -> str:
    return _slug(re.sub(r"\s*\([^)]*\)", "", name))


def _to_trait(e: dict[str, str]) -> dict[str, Any]:
    return {"key": _entry_key(e["name"]), "name": e["name"], "description": e["description"]}


def _to_reaction(e: dict[str, str]) -> dict[str, Any]:
    desc = e["description"]
    trigger = ""
    m = re.search(r"Trigger:\s*(.+?)(?:\s+Response:|\s*$)", desc)
    if m:
        trigger = m.group(1).strip()
    return {
        "key": _entry_key(e["name"]),
        "name": e["name"],
        "description": desc,
        "trigger": trigger,
    }


def _to_action(e: dict[str, str], *, bonus: bool = False) -> dict[str, Any]:
    name = e["name"]
    desc = e["description"]
    out: dict[str, Any] = {
        "key": _entry_key(name),
        "name": name,
        "description": ("Bonus Action. " + desc) if bonus else desc,
    }
    # Usage detection from name parenthetical.
    rec = RECHARGE_RE.search(name) or RECHARGE_RE.search(desc[:120])
    if rec:
        out["usage"] = "recharge"
        out["recharge"] = rec.group(1)
    else:
        per = PER_DAY_RE.search(name) or PER_DAY_RE.search(desc[:120])
        if per:
            out["usage"] = "per_day"
            out["uses_max"] = int(per.group(1))
            out["uses_remaining"] = int(per.group(1))
    attack = _parse_attack(desc)
    if attack:
        out["attack"] = attack
    return out


def _parse_legendary_entries(entries: list[dict[str, str]]) -> tuple[list[dict[str, Any]], int | None]:
    """Pull out the per-round budget and convert remaining entries to actions."""
    per_round: int | None = None
    out: list[dict[str, Any]] = []
    for e in entries:
        name_low = e["name"].lower()
        if name_low.startswith("legendary action uses"):
            # "Legendary Action Uses: 3 (4 in Lair)" — name itself carries the budget.
            m = re.search(r"(\d+)", e["name"])
            if m:
                per_round = int(m.group(1))
            continue
        cost = 1
        m = re.search(r"\(\s*Costs?\s+(\d+)\s+Actions?\s*\)", e["name"], re.IGNORECASE)
        if m:
            cost = int(m.group(1))
        attack = _parse_attack(e["description"])
        rec = {
            "key": _entry_key(e["name"]),
            "name": e["name"],
            "description": e["description"],
            "cost": cost,
        }
        if attack:
            rec["attack"] = attack
        out.append(rec)
    return out, per_round


# --------------------------------------------------------------------- #
# Attack-block parsing
# --------------------------------------------------------------------- #

def _parse_attack(desc: str) -> dict[str, Any] | None:
    attack: dict[str, Any] = {}
    melee = MELEE_ATTACK_RE.search(desc)
    ranged = RANGED_ATTACK_RE.search(desc)
    save = SAVE_RE.search(desc)
    if melee:
        attack["kind"] = "melee"
        attack["to_hit"] = int(melee.group(1))
        rm = REACH_RE.search(desc)
        if rm:
            attack["reach"] = int(rm.group(1))
    elif ranged:
        attack["kind"] = "ranged"
        attack["to_hit"] = int(ranged.group(1))
        rm = RANGE_RE.search(desc)
        if rm:
            attack["range_normal"] = int(rm.group(1))
            if rm.group(2):
                attack["range_long"] = int(rm.group(2))
    elif save:
        attack["kind"] = "spell"
        attack["save_ability"] = ABILITIES_LONG[save.group(1).lower()]
        attack["save_dc"] = int(save.group(2))
        if HALF_ON_SAVE_RE.search(desc):
            attack["half_on_save"] = True
    else:
        return None

    dmg = HIT_DAMAGE_RE.search(desc)
    if dmg:
        attack["damage"] = dmg.group(1).replace(" ", "")
        attack["damage_type"] = dmg.group(2).lower()
        if dmg.group(3):
            attack["extra_damage"] = dmg.group(3).replace(" ", "")
            attack["extra_damage_type"] = dmg.group(4).lower()
    elif attack.get("kind") == "spell":
        # Look for "<dice> <type> damage" anywhere in the prose.
        m = re.search(
            r"\b(\d+d\d+(?:\s*\+\s*\d+)?)\s+(?:[A-Za-z\- ]{0,40}?)?(" +
            "|".join(DAMAGE_TYPES) + r")\s+damage",
            desc, re.IGNORECASE,
        )
        if m:
            attack["damage"] = m.group(1).replace(" ", "")
            attack["damage_type"] = m.group(2).lower()

    return attack or None


# --------------------------------------------------------------------- #
# Misc
# --------------------------------------------------------------------- #

def _slug(name: str) -> str:
    s = name.lower()
    s = re.sub(r"['']", "", s)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


# --------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------- #

def main() -> int:
    if not SRC.exists():
        print(f"missing source: {SRC}", file=sys.stderr)
        return 1
    text = load_monster_section(SRC)
    blocks = split_blocks(text)

    monsters: dict[str, dict[str, Any]] = {}
    skipped: list[str] = []
    for name, body in blocks:
        m = parse_block(name, body)
        if m is None:
            skipped.append(name)
            continue
        key = m["key"]
        if key in monsters:
            key = f"{key}_{len(monsters)}"
            m["key"] = key
        monsters[key] = m

    DST.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_doc": (
            "Structured monster catalog parsed from SRD 5.2.1. Generated "
            "by tools/parse_srd_monsters.py; do not edit by hand. Each "
            "entry conforms (best-effort) to ai_dm.game.monster_state."
            "StatBlock; bonus actions are merged into 'actions' with a "
            "'Bonus Action.' prefix in the description."
        ),
        "_count": len(monsters),
        "monsters": monsters,
    }
    DST.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"wrote {len(monsters)} monsters to {DST}")
    if skipped:
        print(f"skipped {len(skipped)}: " + ", ".join(skipped[:10])
              + ("..." if len(skipped) > 10 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

