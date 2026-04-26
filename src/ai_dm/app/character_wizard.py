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

from ai_dm.app.character_equipment import (
    apply_kit,
    apply_purchases,
    load_items_catalog,
)
from ai_dm.app.character_features import features_for
from ai_dm.app.character_spells import (
    available_spells,
    pick_starting_spells,
)
from ai_dm.campaign.pack import CampaignPack
from ai_dm.persistence.atomic_writer import atomic_write_json
from ai_dm.rules.abilities import ability_mods as _ability_mods
from ai_dm.rules.abilities import saving_throw_mod as _saving_throw_mod
from ai_dm.rules.spell_progression import (
    proficiency_bonus_for,
    spellcasting_block,
)

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
    # When set, this archetype is a spellcaster.  ``class_key`` names
    # the underlying SRD class (``"sorcerer" | "wizard" | "warlock" |
    # ...``); the per-level cantrip / spell / slot caps are pulled from
    # ``ai_dm.rules.spell_progression`` rather than hard-coded here, so
    # leveling up the character automatically grows their allotment.
    # Non-casters leave this ``None``.
    class_key: str | None = None
    # Spell-preparation family. ``"known"`` (sorcerer/warlock) → learned
    # spells are immutable. ``"prepared"`` (cleric/druid/wizard/bard/
    # paladin/ranger) → larger spellbook + daily prepared subset. When
    # ``class_key`` is set this is derived automatically from the SRD
    # progression and the explicit value here serves only as an override.
    casting_style: str | None = None  # "known" | "prepared" | None
    # Saving-throw proficiencies for this archetype's underlying class.
    # SRD 5.2 grants two per class. Defaults match the common mappings:
    #   warrior   → str, con   (fighter)
    #   rogue     → dex, int
    #   scholar   → int, wis   (wizard)
    #   witch     → wis, cha   (warlock)
    saving_throw_profs: tuple[str, ...] = ()


def _spellcasting_for(arch: "Archetype", level: int) -> dict[str, Any] | None:
    """Resolve the spellcasting block for ``arch`` at the given level.

    Reads from the structured SRD progression catalog when the archetype
    declares a ``class_key``; returns ``None`` for non-casters.
    """
    if not arch.class_key:
        return None
    block = spellcasting_block(arch.class_key, level)
    if block and arch.casting_style:
        block = dict(block)
        block["casting_style"] = arch.casting_style
    return block


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
        # Witch maps to the SRD Warlock — Pact Magic slots, charisma, "known"
        # spell list. All per-level caps come from the progression table.
        class_key="warlock",
        casting_style="known",
        saving_throw_profs=("wis", "cha"),
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
        saving_throw_profs=("dex", "int"),
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
        saving_throw_profs=("str", "con"),
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
        # Scholar maps to the SRD Wizard — intelligence, "prepared" spellbook.
        class_key="wizard",
        casting_style="prepared",
        saving_throw_profs=("int", "wis"),
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
    *,
    purchases: list[str | dict[str, Any]] | None = None,
    cantrip_picks: list[str] | None = None,
    spell_picks: list[str] | None = None,
    pack: CampaignPack | None = None,
    level: int = 1,
) -> dict[str, Any]:
    """Build a character-sheet dict from wizard answers. Pure.

    ``purchases`` is an optional list of item ids (or ``{"id":..., "qty":...}``
    dicts) bought with the archetype's starting-gold budget; the unspent
    budget is added to ``currency.gp``. ``cantrip_picks`` / ``spell_picks``
    are spell ids; ignored for non-casters. ``pack`` opts into the
    per-pack rules overlay (themed items / spells / kits / features).

    ``level`` (default 1) controls the per-level allotment: cantrips
    known, spells known/prepared, spell slots, and proficiency bonus
    are all derived from the SRD progression catalog
    (``ai_dm.rules.spell_progression``) keyed off the archetype's
    underlying SRD class.
    """
    arch = ARCHETYPES[archetype_key]
    bg = BACKGROUNDS[background_key]
    # Clamp into the SRD-supported range so downstream consumers (and the
    # serialised sheet itself) can never carry a 0 or 99.
    level = max(1, min(20, int(level)))

    abilities = dict(arch.abilities)
    if stat_bump:
        bump = stat_bump.lower().strip()
        if bump in _ABILITY_KEYS:
            abilities[bump] = abilities[bump] + 1

    # Starting kit + optional purchases.
    inventory, currency, budget = apply_kit(archetype_key, pack=pack)
    if purchases or budget:
        inventory, currency, _ = apply_purchases(
            inventory, currency, budget, purchases or [], pack=pack,
        )

    # Per-level spellcasting allotment (cantrip/spell caps + slots).
    spellcasting = _spellcasting_for(arch, level)

    # Spells (no-op for non-casters).
    spells_block, _ = pick_starting_spells(
        archetype_key,
        cantrip_picks,
        spell_picks,
        spellcasting=spellcasting,
        casting_style=arch.casting_style,  # type: ignore[arg-type]
        pack=pack,
    )

    # Class + background structured features.
    abilities_features = features_for(archetype_key, background_key, pack=pack)

    pb = (
        proficiency_bonus_for(arch.class_key, level) if arch.class_key else 2
    )

    ab_mods = _ability_mods(abilities)
    saves = _saving_throw_mod(
        abilities,
        proficiency_bonus=pb,
        proficient_in=arch.saving_throw_profs,
    )

    return {
        "id": pc_id,
        "name": name,
        "pronouns": "they/them",
        "class": arch.label,
        "level": int(level),
        "background": bg.label,
        "alignment": "neutral",
        "voice": "en-GB-SoniaNeural",
        "appearance": "",
        "personality": bg.personality,
        "abilities": abilities,
        "ability_mods": ab_mods,
        "saving_throws": saves,
        "saving_throw_profs": list(arch.saving_throw_profs),
        "ac": arch.ac,
        "hp": {"current": arch.hp, "max": arch.hp, "temp": 0},
        "speed": 30,
        "proficiency_bonus": pb,
        "languages": ["Common"],
        "features": list(arch.features),
        "abilities_features": abilities_features,
        "inventory": inventory,
        "currency": currency,
        "spells": spells_block,
        "spellcasting": dict(spellcasting) if spellcasting else None,
        "conditions": [],
        "resistances": [],
        "vulnerabilities": [],
        "immunities": [],
        "exhaustion": 0,
        "death_saves": {
            "successes": 0, "failures": 0, "stable": False, "dead": False,
        },
        "notes": bg.blurb,
    }


def validate_response(payload: dict[str, Any]) -> list[str]:
    """Validate a remote wizard response. Returns list of human-readable
    error messages — empty list means OK.

    Checks:
      * ``name`` is non-empty after stripping (length >= 2, <= 60).
      * ``archetype`` is a known key in :data:`ARCHETYPES`.
      * ``background`` is a known key in :data:`BACKGROUNDS`.
      * ``stat_bump`` (optional) is either falsy or a known ability key.
    """
    errors: list[str] = []
    name = str(payload.get("name") or "").strip()
    if len(name) < 2:
        errors.append("Name must be at least 2 characters.")
    elif len(name) > 60:
        errors.append("Name must be at most 60 characters.")

    archetype = str(payload.get("archetype") or "").strip().lower()
    if not archetype:
        errors.append("Archetype is required.")
    elif archetype not in ARCHETYPES:
        valid = ", ".join(sorted(ARCHETYPES))
        errors.append(f"Unknown archetype {archetype!r}. Pick one of: {valid}.")

    background = str(payload.get("background") or "").strip().lower()
    if not background:
        errors.append("Background is required.")
    elif background not in BACKGROUNDS:
        valid = ", ".join(sorted(BACKGROUNDS))
        errors.append(f"Unknown background {background!r}. Pick one of: {valid}.")

    bump = payload.get("stat_bump")
    if bump:
        bump_norm = str(bump).strip().lower()
        if bump_norm and bump_norm not in _ABILITY_KEYS:
            valid = ", ".join(_ABILITY_KEYS)
            errors.append(f"Unknown stat bump {bump_norm!r}. Pick one of: {valid}.")

    # Optional: purchases / spell picks. We only sanity-check the shape
    # and per-archetype caps; build_sheet performs the substantive
    # budget / catalog validation and silently drops invalid entries.
    purchases = payload.get("purchases")
    if purchases is not None and not isinstance(purchases, list):
        errors.append("purchases must be a list of item ids.")

    arch = ARCHETYPES.get(archetype) if archetype in ARCHETYPES else None
    sc = _spellcasting_for(arch, level=1) if arch else None

    def _check_spell_list(field: str, cap_key: str) -> None:
        picks = payload.get(field)
        if picks is None:
            return
        if not isinstance(picks, list):
            errors.append(f"{field} must be a list of spell ids.")
            return
        if not sc:
            if picks:
                errors.append(
                    f"{field} given but archetype {archetype!r} is not a spellcaster."
                )
            return
        cap = int(sc.get(cap_key, 0))
        if len(picks) > cap:
            errors.append(
                f"Too many {field}: {len(picks)} picked but archetype cap is {cap}."
            )

    _check_spell_list("cantrip_picks", "cantrips_known")
    _check_spell_list("spell_picks", "spells_known")

    return errors


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
    pack: CampaignPack | None = None,
) -> dict[str, Any]:
    """Run the 5-step wizard. Returns the built sheet (not yet written).

    ``pack`` opts into per-pack catalog overlays (themed gear / spells)
    surfaced in the kit preview, shopping menu, and spell pickers.
    """
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

        sheet = build_sheet(resolved_id, name, archetype_key, stat_bump, background_key, pack=pack)

        # Step 5 — Starting kit preview
        print_fn("")
        print_fn("=== Starting kit ===")
        for it in sheet.get("inventory", []):
            tag = " (equipped)" if it.get("equipped") else ""
            qty = it.get("qty", 1)
            qty_str = f" x{qty}" if qty != 1 else ""
            print_fn(f"  • {it.get('name')}{qty_str}{tag}")
        cur = sheet.get("currency") or {}
        print_fn(f"  Gold on hand: {cur.get('gp', 0)} gp")

        # Step 6 — Optional shopping with starting budget
        purchases: list[str] = []
        _, _, budget = apply_kit(archetype_key, pack=pack)
        if budget > 0 and _ask_yes_no(
            f"Spend up to {budget} gp on extra gear now?",
            input_fn=input_fn,
            default=False,
        ):
            catalog = load_items_catalog(pack=pack)
            affordable = sorted(
                [(i, r) for i, r in catalog.items() if int(r.get("value_gp", 0)) <= budget],
                key=lambda kv: kv[1].get("name", kv[0]),
            )
            remaining = budget
            print_fn("(type item id or number to add; 'done' when finished)")
            while True:
                print_fn(f"  Remaining: {remaining} gp")
                for i, (iid, rec) in enumerate(affordable, 1):
                    if int(rec.get("value_gp", 0)) > remaining:
                        continue
                    print_fn(f"  {i:>2}. {rec.get('name')} ({rec.get('value_gp', 0)} gp) [{iid}]")
                raw = _ask("> ", input_fn=input_fn).strip().lower()
                if not raw or raw in {"done", "0", "skip"}:
                    break
                pick_id = None
                if raw.isdigit():
                    idx = int(raw)
                    if 1 <= idx <= len(affordable):
                        pick_id = affordable[idx - 1][0]
                elif raw in catalog:
                    pick_id = raw
                if pick_id is None:
                    print_fn("  (unknown — try again)")
                    continue
                cost = int(catalog[pick_id].get("value_gp", 0))
                if cost > remaining:
                    print_fn("  (too expensive)")
                    continue
                purchases.append(pick_id)
                remaining -= cost

        # Step 7 — Spells (casters only)
        arch_spellcasting = _spellcasting_for(arch, level=1)
        cantrip_picks: list[str] = []
        spell_picks: list[str] = []
        if arch_spellcasting:
            print_fn("")
            print_fn("=== Spells ===")
            cantrips_cap = int(arch_spellcasting.get("cantrips_known", 0))
            spells_cap = int(arch_spellcasting.get("spells_known", 0))

            def _pick_spells(level: int, cap: int) -> list[str]:
                if cap <= 0:
                    return []
                pool = available_spells(archetype_key, level, pack=pack)
                if not pool:
                    return []
                label = "cantrip" if level == 0 else f"level-{level} spell"
                print_fn(f"Pick up to {cap} {label}{'s' if cap > 1 else ''}:")
                for i, (sid, rec) in enumerate(pool, 1):
                    print_fn(f"  {i:>2}. {rec.get('name')} [{sid}] — {rec.get('description', '')[:80]}")
                print_fn("(type ids or numbers separated by commas; blank to skip)")
                raw = _ask("> ", input_fn=input_fn).strip()
                if not raw:
                    return []
                picks: list[str] = []
                for tok in raw.split(","):
                    tok = tok.strip().lower()
                    if not tok:
                        continue
                    if tok.isdigit():
                        idx = int(tok)
                        if 1 <= idx <= len(pool):
                            picks.append(pool[idx - 1][0])
                    else:
                        for sid, _rec in pool:
                            if sid == tok:
                                picks.append(sid)
                                break
                    if len(picks) >= cap:
                        break
                return picks

            cantrip_picks = _pick_spells(0, cantrips_cap)
            spell_picks = _pick_spells(1, spells_cap)

        if purchases or cantrip_picks or spell_picks:
            sheet = build_sheet(
                resolved_id, name, archetype_key, stat_bump, background_key,
                purchases=purchases or None,
                cantrip_picks=cantrip_picks or None,
                spell_picks=spell_picks or None,
                pack=pack,
            )

        # Step 8 — Confirm
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


# --------------------------------------------------------------------- #
# Level-up: re-derive per-level allotments from the SRD progression.
# --------------------------------------------------------------------- #


def _archetype_for_sheet(sheet: dict[str, Any]) -> Archetype | None:
    """Best-effort lookup of the :class:`Archetype` that produced ``sheet``."""
    label = str(sheet.get("class") or "").strip().lower()
    if not label:
        return None
    for arch in ARCHETYPES.values():
        if arch.label.lower() == label or arch.key == label:
            return arch
    return None


def apply_level(sheet: dict[str, Any], new_level: int) -> dict[str, Any]:
    """Return a copy of ``sheet`` re-derived at ``new_level``.

    Updates ``level``, ``proficiency_bonus``, ``spellcasting`` (cantrip /
    spell caps + slot dict) and the slot maxes inside ``spells.slots``.
    Existing learned cantrips / spells / spellbook entries are preserved
    (so leveling up never *forgets* a spell), but excess picks beyond the
    new caps are kept on the sheet — :func:`character_spells.pick_starting_spells`
    is the place to enforce caps when accepting *new* picks.

    Slot ``value`` (current remaining) is bumped to the new ``max`` for
    any slot whose max grew, and clamped down for any slot whose max
    shrank (defensive — slot maxes never shrink in vanilla SRD, but
    house-ruled progressions might).
    """
    if not isinstance(sheet, dict):
        raise TypeError("sheet must be a dict")
    new_level = max(1, min(20, int(new_level)))
    arch = _archetype_for_sheet(sheet)
    out = dict(sheet)
    out["level"] = new_level
    if arch is None:
        # No known archetype → just update the level + a default PB curve.
        out["proficiency_bonus"] = 2 + (new_level - 1) // 4
        return out

    out["proficiency_bonus"] = (
        proficiency_bonus_for(arch.class_key, new_level)
        if arch.class_key
        else 2 + (new_level - 1) // 4
    )

    # Re-derive saving-throw bonuses (PB grew → proficient saves grew too).
    abilities = out.get("abilities") or {}
    if abilities:
        out["ability_mods"] = _ability_mods(abilities)
        out["saving_throws"] = _saving_throw_mod(
            abilities,
            proficiency_bonus=out["proficiency_bonus"],
            proficient_in=arch.saving_throw_profs,
        )

    new_block = _spellcasting_for(arch, new_level)
    out["spellcasting"] = dict(new_block) if new_block else None

    # Update the slot maxes inside the existing spells block (preserve
    # every other field — known spells, spellbook, cantrips_known list).
    spells = dict(out.get("spells") or {})
    if new_block:
        new_slots = new_block.get("slots") or {}
        materialised: dict[str, dict[str, int]] = {}
        existing = spells.get("slots") or {}
        for slot_level, mx in new_slots.items():
            cur = existing.get(slot_level) or {}
            current_value = int(cur.get("value", mx))
            # Grow the remaining value for newly-unlocked slots; clamp it
            # if the cap shrank for any reason.
            new_value = min(int(mx), current_value if slot_level in existing else int(mx))
            if slot_level not in existing:
                new_value = int(mx)
            materialised[slot_level] = {"max": int(mx), "value": int(new_value)}
        spells["slots"] = materialised
        out["spells"] = spells
    elif spells:
        # Non-caster after re-derivation — drop slots block defensively.
        spells["slots"] = {}
        out["spells"] = spells
    return out


