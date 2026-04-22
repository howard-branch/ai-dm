"""Guided character-creation wizard.

Five fast steps — Name → Archetype → Stats (auto + 1 optional bump) →
Background → Confirm — that produce a JSON character sheet matching the
shape consumed by ``bootstrap._load_character_sheet`` and the live
runtime (``id``, ``name``, ``class``, ``abilities``, ``hp`` etc.).

Design constraints:
    * Stdlib only (``input``/``print``) — no extra deps.
    * Pure ``build_sheet`` helper for testability; ``run_wizard`` is the
      thin IO loop.
    * Writes only into ``pack.state.characters`` (the writable state
      dir). Never touches ``pack.paths.characters_seed``.
    * Idempotent: ``needs_wizard`` returns False when a live sheet
      already exists for the configured PC id, OR a seed file exists
      (so hand-crafted packs like Morgana / AOTD are preserved).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ai_dm.campaign.pack import CampaignPack
from ai_dm.persistence.atomic_writer import atomic_write_json

logger = logging.getLogger("ai_dm.app.character_wizard")


# --------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------- #


_ABILITY_KEYS: tuple[str, ...] = ("str", "dex", "con", "int", "wis", "cha")


@dataclass(frozen=True)
class Archetype:
    key: str
    label: str           # written into sheet's `class` field
    emoji: str
    blurb: str
    abilities: dict[str, int]
    ac: int
    hp: int
    features: list[dict[str, str]]


@dataclass(frozen=True)
class Background:
    key: str
    label: str
    blurb: str           # short flavour shown in menu + stored in `notes`
    personality: str     # seed for `personality` field


ARCHETYPES: dict[str, Archetype] = {
    "witch": Archetype(
        key="witch",
        label="Witch",
        emoji="🧙",
        blurb="Magic and charisma. Hexes, glamours, and old pacts.",
        abilities={"str": 8, "dex": 13, "con": 13, "int": 12, "wis": 12, "cha": 16},
        ac=12,
        hp=8,
        features=[
            {"name": "Hex", "summary": "Mark a target; deal extra necrotic on hit."},
            {"name": "Arcane Focus", "summary": "Channel spells through a personal token."},
        ],
    ),
    "rogue": Archetype(
        key="rogue",
        label="Rogue",
        emoji="🗡️",
        blurb="Stealth and dexterity. Strike from shadow; vanish before reply.",
        abilities={"str": 10, "dex": 16, "con": 13, "int": 12, "wis": 12, "cha": 11},
        ac=14,
        hp=10,
        features=[
            {"name": "Sneak Attack 1d6", "summary": "Bonus damage when you have advantage."},
            {"name": "Thieves' Tools", "summary": "Proficient with locks and traps."},
        ],
    ),
    "warrior": Archetype(
        key="warrior",
        label="Warrior",
        emoji="🛡️",
        blurb="Strength and durability. Stand at the front; take the hits.",
        abilities={"str": 16, "dex": 12, "con": 15, "int": 10, "wis": 11, "cha": 10},
        ac=16,
        hp=12,
        features=[
            {"name": "Second Wind", "summary": "Once per rest, recover 1d10 HP as a bonus action."},
            {"name": "Martial Training", "summary": "Proficient with longsword and shield."},
        ],
    ),
    "scholar": Archetype(
        key="scholar",
        label="Scholar",
        emoji="📜",
        blurb="Intellect and knowledge. Read the world like a book.",
        abilities={"str": 9, "dex": 12, "con": 12, "int": 16, "wis": 14, "cha": 11},
        ac=11,
        hp=8,
        features=[
            {"name": "Lore", "summary": "Advantage on history and arcana checks."},
            {"name": "Spellbook", "summary": "Records and prepares rituals."},
        ],
    ),
}


BACKGROUNDS: dict[str, Background] = {
    "exiled_noble": Background(
        key="exiled_noble",
        label="Exiled Noble",
        blurb="Cast out from a great house; carries a signet ring and old grudges.",
        personality="Polished manners, sharp pride, slow to forgive.",
    ),
    "forbidden_scholar": Background(
        key="forbidden_scholar",
        label="Forbidden Scholar",
        blurb="Studied what was sealed; hunted by a former order.",
        personality="Quiet, precise, addicted to the next sealed door.",
    ),
    "wandering_mage": Background(
        key="wandering_mage",
        label="Wandering Mage",
        blurb="No tower, no master — a road and a staff.",
        personality="Easy company, evasive about the past.",
    ),
    "hedge_witch": Background(
        key="hedge_witch",
        label="Hedge Witch",
        blurb="Village-edge healer; trades in herbs, omens, and quiet favours.",
        personality="Patient, blunt, suspicious of cities.",
    ),
    "sellsword": Background(
        key="sellsword",
        label="Sellsword",
        blurb="Sold a blade for too long; looking for a cause that pays in meaning.",
        personality="Dry humour, careful with money, careless with promises.",
    ),
}


# --------------------------------------------------------------------- #
# Pure builders
# --------------------------------------------------------------------- #


def build_sheet(
    pc_id: str,
    name: str,
    archetype_key: str,
    stat_bump: str | None,
    background_key: str,
) -> dict[str, Any]:
    """Build a character-sheet dict from wizard answers. Pure."""
    arch = ARCHETYPES[archetype_key]
    bg = BACKGROUNDS[background_key]

    abilities = dict(arch.abilities)
    if stat_bump:
        bump = stat_bump.lower().strip()
        if bump in _ABILITY_KEYS:
            abilities[bump] = abilities[bump] + 1

    return {
        "id": pc_id,
        "name": name,
        "pronouns": "they/them",
        "class": arch.label,
        "level": 1,
        "background": bg.label,
        "alignment": "neutral",
        "voice": "en-GB-SoniaNeural",
        "appearance": "",
        "personality": bg.personality,
        "abilities": abilities,
        "ac": arch.ac,
        "hp": {"current": arch.hp, "max": arch.hp, "temp": 0},
        "speed": 30,
        "proficiency_bonus": 2,
        "languages": ["Common"],
        "features": list(arch.features),
        "inventory": [],
        "conditions": [],
        "notes": bg.blurb,
    }


# --------------------------------------------------------------------- #
# IO helpers
# --------------------------------------------------------------------- #


_PrintFn = Callable[[str], None]
_InputFn = Callable[[str], str]


def _ask(prompt: str, *, input_fn: _InputFn) -> str:
    return input_fn(prompt).strip()


def _ask_choice(
    prompt: str,
    options: list[tuple[str, str]],   # (key, label)
    *,
    input_fn: _InputFn,
    print_fn: _PrintFn,
    allow_skip: bool = False,
) -> str | None:
    """Numbered-menu choice. Returns the key (or ``None`` if skipped)."""
    while True:
        print_fn(prompt)
        for i, (_key, label) in enumerate(options, 1):
            print_fn(f"  {i}. {label}")
        if allow_skip:
            print_fn("  0. Skip")
        raw = _ask("> ", input_fn=input_fn)
        if allow_skip and raw in {"0", "", "skip"}:
            return None
        # Accept either the index or the key itself.
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(options):
                return options[idx - 1][0]
        for key, _label in options:
            if raw.lower() == key.lower():
                return key
        print_fn("  (unrecognised — try again)")


def _ask_yes_no(prompt: str, *, input_fn: _InputFn, default: bool = True) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    raw = _ask(prompt + suffix, input_fn=input_fn).lower()
    if not raw:
        return default
    return raw.startswith("y")


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s or "hero"


# --------------------------------------------------------------------- #
# Wizard
# --------------------------------------------------------------------- #


def run_wizard(
    pc_id: str | None = None,
    *,
    input_fn: _InputFn = input,
    print_fn: _PrintFn = print,
) -> dict[str, Any]:
    """Run the 5-step wizard. Returns the built sheet (not yet written)."""
    print_fn("")
    print_fn("=== Create your character ===")
    print_fn("")

    while True:
        # Step 1 — Name
        name = ""
        while not name:
            name = _ask("Enter your name: ", input_fn=input_fn)
            if not name:
                print_fn("  (a name is required)")
        resolved_id = pc_id or _slugify(name)

        # Step 2 — Archetype
        print_fn("")
        archetype_options = [
            (a.key, f"{a.emoji}  {a.label} — {a.blurb}") for a in ARCHETYPES.values()
        ]
        archetype_key = _ask_choice(
            "Choose your archetype:",
            archetype_options,
            input_fn=input_fn,
            print_fn=print_fn,
        )
        assert archetype_key is not None
        arch = ARCHETYPES[archetype_key]

        # Step 3 — Stats (show, offer one bump)
        print_fn("")
        print_fn(f"Starting stats for {arch.label}:")
        for k in _ABILITY_KEYS:
            print_fn(f"  {k.upper()} {arch.abilities[k]}")
        print_fn("")
        stat_options = [(k, k.upper()) for k in _ABILITY_KEYS]
        stat_bump = _ask_choice(
            "Increase one stat by +1 (or skip):",
            stat_options,
            input_fn=input_fn,
            print_fn=print_fn,
            allow_skip=True,
        )

        # Step 4 — Background
        print_fn("")
        bg_options = [(b.key, f"{b.label} — {b.blurb}") for b in BACKGROUNDS.values()]
        background_key = _ask_choice(
            "Choose a background:",
            bg_options,
            input_fn=input_fn,
            print_fn=print_fn,
        )
        assert background_key is not None

        sheet = build_sheet(resolved_id, name, archetype_key, stat_bump, background_key)

        # Step 5 — Confirm
        print_fn("")
        print_fn("=== Summary ===")
        bumped = sheet["abilities"]
        top = sorted(bumped.items(), key=lambda kv: -kv[1])[:2]
        top_str = ", ".join(f"{k.upper()} {v}" for k, v in top)
        print_fn(f"  {sheet['name']} – {sheet['class']}")
        print_fn(f"  {top_str}")
        print_fn(f"  Background: {sheet['background']}")
        print_fn("")
        if _ask_yes_no("Start the game with this character?", input_fn=input_fn, default=True):
            print_fn("")
            return sheet
        print_fn("")
        print_fn("(starting over)")
        print_fn("")


# --------------------------------------------------------------------- #
# Persistence + invocation gate
# --------------------------------------------------------------------- #


def sheet_path(pack: CampaignPack, pc_id: str) -> Path:
    return pack.state.characters / f"{pc_id}.json"


def needs_wizard(pack: CampaignPack, pc_id: str | None) -> bool:
    """True iff the wizard should run for ``pc_id``.

    Skipped when:
      * ``pc_id`` is missing, or
      * a live sheet exists at ``state.characters/{pc_id}.json``, or
      * a seed sheet exists at ``paths.characters_seed/{pc_id}.json``
        (preserves hand-crafted packs).
    """
    if not pc_id:
        return False
    if sheet_path(pack, pc_id).exists():
        return False
    seed = pack.paths.characters_seed / f"{pc_id}.json"
    if seed.exists():
        return False
    return True


def write_sheet(pack: CampaignPack, pc_id: str, sheet: dict[str, Any]) -> Path:
    target = sheet_path(pack, pc_id)
    atomic_write_json(target, sheet)
    return target

