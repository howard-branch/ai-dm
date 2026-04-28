"""Spell domain models — SRD 5.2 magic rules beyond the bare spell list.

This module is the *typed* counterpart to the JSON spell catalog at
``assets/srd5_2/spells.json`` (and the wizard starter catalog at
``assets/rules/dnd5e_spells.json``). It captures the magic-system
concerns the rules engine actually has to reason about — slot levels,
ritual casting, components and material costs/consumption, scaling
(upcasting), spell attack rolls, spell save DCs, concentration, and
class spell lists — so callers can stop hand-rolling dicts.

Design
------
* Pydantic v2, ``extra="forbid"`` everywhere so any drift between this
  schema and on-disk JSON (or a legacy character sheet) fails loudly
  during tests.
* Forward-compatible: every field except the bare identity tuple
  (``id``/``name``/``level``) has a sensible default so existing
  campaign content keeps loading after a schema bump.
* Pure data + small derivation helpers (``upcast_dice``,
  ``slot_levels_castable_at`` …). Catalog I/O lives in
  :mod:`ai_dm.app.character_spells`; runtime accounting (slot spend,
  concentration, prepared list) lives on
  :class:`ai_dm.game.combatant_state.CombatantState`.

The mapping ``id (str)`` is the canonical handle used everywhere else
(combatant ``known_spells`` / ``prepared_spells`` / ``cantrips``,
``Concentration.spell_id``, etc.). Loaders should round-trip through
``Spell.model_validate(dict)`` so unknown fields trip the model rather
than silently disappearing.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------- #
# Aliases / constants
# --------------------------------------------------------------------- #

CastingTimeUnit = Literal[
    "action", "bonus_action", "reaction",
    "minute", "minutes", "hour", "hours",
]
DurationKind = Literal[
    "instantaneous", "round", "rounds", "minute", "minutes",
    "hour", "hours", "day", "days", "until_dispelled", "special",
]
SchoolKey = Literal[
    "abjuration", "conjuration", "divination", "enchantment",
    "evocation", "illusion", "necromancy", "transmutation",
    # Short codes used by the legacy wizard catalog
    "abj", "con", "div", "enc", "evo", "ill", "nec", "trs",
]
SpellAttackKind = Literal["melee", "ranged", "spell_attack"]
SaveAbility = Literal["str", "dex", "con", "int", "wis", "cha"]
TargetingKind = Literal[
    "self", "single", "single_creature", "multi",
    "radius", "sphere", "cube", "cone", "line", "cylinder", "point",
]
CastingStyle = Literal["known", "prepared"]

# Slot-level cap — SRD spell slots run 1..9, with cantrips at 0.
MIN_SPELL_LEVEL = 0
MAX_SPELL_LEVEL = 9


# --------------------------------------------------------------------- #
# Sub-models
# --------------------------------------------------------------------- #


class SpellComponents(BaseModel):
    """V/S/M components plus material-cost / consumption flags.

    SRD wording:

    * ``v`` — verbal: clear voice required (silenced casters can't cast).
    * ``s`` — somatic: a free hand for gestures.
    * ``m`` — material: a focus or pouch covers free components; a
      ``material_cost_gp`` >0 means a *specific* component the focus
      cannot substitute. ``material_consumed`` means the component is
      destroyed by a successful casting (Revivify's diamond, Glyph of
      Warding's incense, …).
    """

    model_config = ConfigDict(extra="forbid")

    v: bool = False
    s: bool = False
    m: bool = False
    material: str | None = None  # human-readable description
    material_cost_gp: float = 0.0
    material_consumed: bool = False

    @model_validator(mode="after")
    def _consistency(self) -> "SpellComponents":
        # If a material description or cost is given, infer m=True so
        # legacy catalog rows (which often omit ``m``) stay sane.
        if (self.material or self.material_cost_gp > 0 or self.material_consumed) and not self.m:
            self.m = True
        return self


class SpellCastingTime(BaseModel):
    """Casting time + ritual flag.

    A spell with ``ritual=True`` may also be cast as a ritual (10 min
    longer, no slot consumed) by an eligible caster. Whether the caster
    is *eligible* lives on :class:`CombatantState.ritual_caster`.
    """

    model_config = ConfigDict(extra="forbid")

    unit: CastingTimeUnit = "action"
    amount: int = 1  # e.g. amount=10, unit="minutes"
    ritual: bool = False
    raw: str | None = None  # original SRD prose, kept for display


class SpellDuration(BaseModel):
    """Duration + concentration flag.

    ``concentration=True`` means the caster must concentrate (see
    :mod:`ai_dm.rules.concentration`). The duration unit/amount apply
    while concentration holds (or until dispelled).
    """

    model_config = ConfigDict(extra="forbid")

    kind: DurationKind = "instantaneous"
    amount: int = 0
    concentration: bool = False
    raw: str | None = None


class SpellTargeting(BaseModel):
    """Where / how the spell projects itself onto the battlefield."""

    model_config = ConfigDict(extra="forbid")

    kind: TargetingKind = "single"
    range_ft: int | None = None
    radius_ft: int | None = None
    length_ft: int | None = None
    width_ft: int | None = None
    anchor: Literal["caster", "target", "point"] | None = None
    max_targets: int | None = None
    self_only: bool = False


class SpellAttack(BaseModel):
    """Attack-roll metadata for a spell that hits with an attack roll.

    Mutually exclusive with :class:`SpellSave`: spells either roll to
    hit or impose a save (a tiny number do both, in which case both
    blocks may be present).
    """

    model_config = ConfigDict(extra="forbid")

    kind: SpellAttackKind = "spell_attack"
    range: Literal["melee", "ranged"] = "ranged"


class SpellSave(BaseModel):
    """Saving-throw metadata for a spell that imposes a save."""

    model_config = ConfigDict(extra="forbid")

    ability: SaveAbility
    half_on_save: bool = False
    no_effect_on_save: bool = False  # e.g. Hold Person on success


class SpellDamage(BaseModel):
    """Damage payload (single-type or multi-part).

    ``parts`` mirrors the ``dnd5e_spells.json`` shape:
    ``[["1d10", "fire"], ["1d6", "radiant"]]``.
    """

    model_config = ConfigDict(extra="forbid")

    dice: str | None = None  # primary damage, e.g. "8d6"
    type: str | None = None  # e.g. "fire"
    parts: list[tuple[str, str]] = Field(default_factory=list)


class SpellHealing(BaseModel):
    """Healing payload (Cure Wounds, Mass Healing Word, …)."""

    model_config = ConfigDict(extra="forbid")

    dice: str = ""
    bonus_ability_mod: bool = True  # add caster's spellcasting mod


class SpellScaling(BaseModel):
    """Upcasting / cantrip scaling rules.

    * ``cantrip_scaling`` — for level-0 spells, the caster level
      thresholds at which extra dice kick in. SRD cantrips step at
      character levels 5/11/17 by default.
    * ``higher_level`` — for leveled spells cast with a higher slot:
      ``dice_per_slot_above`` (e.g. ``"1d6"``) and the ``base_level``
      the spell is normally prepared at. ``targets_per_slot_above``
      handles spells like *Magic Missile* / *Scorching Ray*.
    * ``description`` keeps the SRD prose verbatim for display.
    """

    model_config = ConfigDict(extra="forbid")

    cantrip_scaling: list[int] = Field(default_factory=list)
    higher_level_dice_per_slot_above: str | None = None
    higher_level_base_level: int | None = None
    higher_level_targets_per_slot_above: int = 0
    higher_level_healing_per_slot_above: str | None = None
    description: str | None = None


# --------------------------------------------------------------------- #
# Top-level Spell
# --------------------------------------------------------------------- #


class Spell(BaseModel):
    """Canonical spell definition.

    Hydrate from a catalog dict via ``Spell.model_validate(rec)``. The
    runtime cast (slot consumption, concentration start, target
    selection) is *not* part of this model — it lives on the rules
    engine and on :class:`CombatantState`.
    """

    model_config = ConfigDict(extra="forbid")

    # --- identity --------------------------------------------------- #
    id: str
    name: str
    level: int = Field(ge=MIN_SPELL_LEVEL, le=MAX_SPELL_LEVEL)
    school: SchoolKey | str = "evocation"

    # --- class lists ----------------------------------------------- #
    # ``classes`` is the SRD source of truth; ``archetypes`` is the
    # wizard-flavour overlay (e.g. "witch" → warlock-ish). Either may
    # be empty (= open to every caster).
    classes: list[str] = Field(default_factory=list)
    archetypes: list[str] = Field(default_factory=list)

    # --- mechanics -------------------------------------------------- #
    casting_time: SpellCastingTime = Field(default_factory=SpellCastingTime)
    components: SpellComponents = Field(default_factory=SpellComponents)
    duration: SpellDuration = Field(default_factory=SpellDuration)
    range: int | str | None = None  # ft; "self"/"touch" kept as strings
    targeting: SpellTargeting | None = None

    # --- effect ----------------------------------------------------- #
    attack: SpellAttack | None = None
    save: SpellSave | None = None
    damage: SpellDamage | None = None
    healing: SpellHealing | None = None
    scaling: SpellScaling | None = None

    # --- bookkeeping ----------------------------------------------- #
    description: str = ""
    schema_version: int = 1

    # ------------------------------------------------------------------ #
    # Convenience predicates
    # ------------------------------------------------------------------ #

    @property
    def is_cantrip(self) -> bool:
        return self.level == 0

    @property
    def is_ritual(self) -> bool:
        return bool(self.casting_time.ritual)

    @property
    def requires_concentration(self) -> bool:
        return bool(self.duration.concentration)

    @property
    def has_costly_material(self) -> bool:
        """True iff casting needs a specific component a focus can't replace."""
        return self.components.m and (
            self.components.material_cost_gp > 0 or self.components.material_consumed
        )

    def is_available_to(self, *, class_key: str | None = None,
                        archetype_key: str | None = None) -> bool:
        """Membership test against the class / archetype lists.

        An empty list means "open to all"; matching against either
        list is enough.
        """
        if class_key and self.classes and class_key in self.classes:
            return True
        if archetype_key and self.archetypes and archetype_key in self.archetypes:
            return True
        # No restriction at all → open.
        return not self.classes and not self.archetypes

    # ------------------------------------------------------------------ #
    # Upcasting helpers
    # ------------------------------------------------------------------ #

    def slot_levels_castable_at(self) -> list[int]:
        """Return the slot levels this spell may be cast at.

        Cantrips cast at level 0 only. Leveled spells may be cast at
        their base level or any higher slot up to 9.
        """
        if self.is_cantrip:
            return [0]
        return list(range(self.level, MAX_SPELL_LEVEL + 1))

    def upcast_dice(self, slot_level: int) -> str | None:
        """Compute the *full* damage dice when cast with ``slot_level``.

        Returns ``None`` when the spell has no damage block or the
        request is invalid (e.g. slot below the spell's base level).
        Falls back to the base dice when no scaling info is present.
        """
        if self.damage is None or not self.damage.dice:
            return None
        if slot_level < self.level:
            return None
        base = self.damage.dice
        scaling = self.scaling
        if (
            scaling is None
            or not scaling.higher_level_dice_per_slot_above
            or scaling.higher_level_base_level is None
        ):
            return base
        bonus_levels = max(0, slot_level - int(scaling.higher_level_base_level))
        if bonus_levels == 0:
            return base
        return _scale_dice(
            base, scaling.higher_level_dice_per_slot_above, bonus_levels
        )

    def cantrip_dice_at_caster_level(self, caster_level: int) -> str | None:
        """Return cantrip dice for a caster of ``caster_level`` (1..20).

        Counts how many ``cantrip_scaling`` thresholds the caster has
        passed and multiplies the base die count accordingly. Defaults
        to the SRD 5/11/17 thresholds when ``cantrip_scaling`` is
        empty.
        """
        if not self.is_cantrip or self.damage is None or not self.damage.dice:
            return None
        thresholds = self.scaling.cantrip_scaling if self.scaling else []
        if not thresholds:
            thresholds = [5, 11, 17]
        steps = sum(1 for t in thresholds if caster_level >= int(t))
        multiplier = 1 + steps
        return _multiply_dice_count(self.damage.dice, multiplier)


# --------------------------------------------------------------------- #
# Class spell lists
# --------------------------------------------------------------------- #


class ClassSpellList(BaseModel):
    """The SRD spell list for one class, indexed by spell level.

    Built by walking the spell catalog once (``Spell.classes``) and
    grouping by level — see :func:`build_class_spell_lists`. Stored as
    a model rather than a bare dict so callers can rely on a stable
    shape and so we can add metadata (subclass overlays, expanded
    lists from features) without breaking call sites.
    """

    model_config = ConfigDict(extra="forbid")

    class_key: str
    by_level: dict[int, list[str]] = Field(default_factory=dict)

    def at_level(self, spell_level: int) -> list[str]:
        return list(self.by_level.get(int(spell_level), ()))

    def all_ids(self) -> list[str]:
        out: list[str] = []
        for lvl in sorted(self.by_level):
            out.extend(self.by_level[lvl])
        return out


def build_class_spell_lists(
    spells: dict[str, dict[str, Any]] | dict[str, "Spell"],
) -> dict[str, ClassSpellList]:
    """Group ``spells`` (id → record) by class for fast list-by-class lookup."""
    out: dict[str, ClassSpellList] = {}
    for sid, rec in spells.items():
        spell = rec if isinstance(rec, Spell) else Spell.model_validate({"id": sid, **rec})
        for cls in spell.classes:
            bucket = out.setdefault(cls, ClassSpellList(class_key=cls, by_level={}))
            bucket.by_level.setdefault(spell.level, []).append(spell.id)
    for lst in out.values():
        for lvl in lst.by_level:
            lst.by_level[lvl].sort()
    return out


# --------------------------------------------------------------------- #
# Caster derivation helpers
# --------------------------------------------------------------------- #


def spell_save_dc(*, ability_mod: int, proficiency_bonus: int) -> int:
    """SRD: 8 + proficiency bonus + spellcasting ability modifier."""
    return 8 + int(proficiency_bonus) + int(ability_mod)


def spell_attack_bonus(*, ability_mod: int, proficiency_bonus: int) -> int:
    """SRD: proficiency bonus + spellcasting ability modifier."""
    return int(proficiency_bonus) + int(ability_mod)


# --------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------- #

_DICE_RE = re.compile(r"^\s*(\d+)\s*[dD]\s*(\d+)\s*([+-]\s*\d+)?\s*$")


def _multiply_dice_count(dice: str, multiplier: int) -> str:
    """Return ``dice`` with its die *count* multiplied by ``multiplier``.

    ``"1d10"`` × 2 → ``"2d10"``. Bonus modifiers are preserved verbatim.
    Falls back to the original string for shapes we don't recognise so
    legacy free-form damage strings keep round-tripping.
    """
    if multiplier <= 1:
        return dice
    m = _DICE_RE.match(dice)
    if not m:
        return dice
    count = int(m.group(1)) * int(multiplier)
    sides = int(m.group(2))
    bonus = (m.group(3) or "").replace(" ", "")
    return f"{count}d{sides}{bonus}"


def _add_dice(base: str, addend: str) -> str:
    """Combine two same-die-size dice strings, e.g. ``2d6`` + ``1d6`` → ``3d6``.

    Different die sizes (or unparseable strings) fall back to a
    concatenated form so callers still get a usable expression.
    """
    bm = _DICE_RE.match(base)
    am = _DICE_RE.match(addend)
    if bm and am and bm.group(2) == am.group(2):
        count = int(bm.group(1)) + int(am.group(1))
        bonus = (bm.group(3) or "").replace(" ", "")
        return f"{count}d{bm.group(2)}{bonus}"
    return f"{base}+{addend}"


def _scale_dice(base: str, per_slot: str, bonus_levels: int) -> str:
    """Add ``per_slot * bonus_levels`` extra dice to ``base``."""
    if bonus_levels <= 0:
        return base
    extra = _multiply_dice_count(per_slot, bonus_levels)
    return _add_dice(base, extra)


__all__ = [
    "CastingStyle",
    "CastingTimeUnit",
    "ClassSpellList",
    "DurationKind",
    "MAX_SPELL_LEVEL",
    "MIN_SPELL_LEVEL",
    "SaveAbility",
    "SchoolKey",
    "Spell",
    "SpellAttack",
    "SpellAttackKind",
    "SpellCastingTime",
    "SpellComponents",
    "SpellDamage",
    "SpellDuration",
    "SpellHealing",
    "SpellSave",
    "SpellScaling",
    "SpellTargeting",
    "TargetingKind",
    "build_class_spell_lists",
    "spell_attack_bonus",
    "spell_save_dc",
]

