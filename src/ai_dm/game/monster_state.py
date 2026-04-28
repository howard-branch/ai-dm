"""Monster / NPC stat-block models.

These dataclasses describe the *immutable* SRD-style stat block for a
monster or NPC — the data the AI/DM needs to understand what a creature
can do. The mutable per-encounter slice (HP, conditions, action economy,
legendary actions remaining …) lives on
:class:`ai_dm.game.combatant_state.CombatantState` and is hydrated from a
:class:`StatBlock` via :meth:`StatBlock.to_combatant`.

Design
------
* Pydantic v2 models, ``extra="forbid"`` so any drift between this
  schema and on-disk JSON fails loudly during tests.
* Pure data — no side effects, no I/O. Loaders/registries live in
  :mod:`ai_dm.game.npc_manager`.
* Forward-compatible: any new field must be optional (default-valued)
  so existing campaign content keeps loading.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ActionUsage = Literal["at_will", "recharge", "per_day", "limited"]
LegendaryUsage = Literal["1", "2", "3"]  # cost in legendary actions

# Canonical SRD CR → XP table (CR 0 .. 30). Listed explicitly because
# the formula has irregular steps at the low end.
CR_TO_XP: dict[float, int] = {
    0.0: 10, 0.125: 25, 0.25: 50, 0.5: 100,
    1: 200, 2: 450, 3: 700, 4: 1100, 5: 1800, 6: 2300, 7: 2900,
    8: 3900, 9: 5000, 10: 5900, 11: 7200, 12: 8400, 13: 10000,
    14: 11500, 15: 13000, 16: 15000, 17: 18000, 18: 20000, 19: 22000,
    20: 25000, 21: 33000, 22: 41000, 23: 50000, 24: 62000, 25: 75000,
    26: 90000, 27: 105000, 28: 120000, 29: 135000, 30: 155000,
}


def cr_to_xp(cr: float | int | str | None) -> int | None:
    """Look up the XP value for a challenge rating.

    Accepts numeric CR (``0.25``, ``5``) or fraction strings
    (``"1/4"``). Returns ``None`` for unknown / out-of-table values
    rather than raising — XP is always optional in our schema.
    """
    if cr is None:
        return None
    if isinstance(cr, str):
        s = cr.strip()
        if "/" in s:
            try:
                num, den = s.split("/", 1)
                cr = float(num) / float(den)
            except (ValueError, ZeroDivisionError):
                return None
        else:
            try:
                cr = float(s)
            except ValueError:
                return None
    try:
        key: float = float(cr)
    except (TypeError, ValueError):
        return None
    # Match integer keys exactly; fractional CRs use float keys.
    if key in CR_TO_XP:
        return CR_TO_XP[key]
    if key.is_integer() and int(key) in CR_TO_XP:  # type: ignore[arg-type]
        return CR_TO_XP[int(key)]  # type: ignore[index]
    return None


# --------------------------------------------------------------------- #
# Sub-models
# --------------------------------------------------------------------- #


class Senses(BaseModel):
    """SRD senses block (all values measured in feet, ``passive_perception`` excepted)."""

    model_config = ConfigDict(extra="forbid")

    blindsight: int = 0
    darkvision: int = 0
    tremorsense: int = 0
    truesight: int = 0
    passive_perception: int = 10
    notes: str | None = None


class Trait(BaseModel):
    """A passive feature on a stat block (Amphibious, Pack Tactics …)."""

    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    description: str = ""


class MonsterAttack(BaseModel):
    """Optional attack-roll / damage payload bundled with a :class:`MonsterAction`.

    Kept separate so an action like *Multiattack* has no attack block,
    while *Bite* has exactly one. Damage strings are SRD dice notation
    (``"2d6+3"``) — the rules engine already knows how to roll them.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["melee", "ranged", "spell"] = "melee"
    to_hit: int = 0
    reach: int | None = None
    range_normal: int | None = None
    range_long: int | None = None
    target: str = "one target"
    damage: str = ""  # e.g. "2d6+3"
    damage_type: str = ""  # e.g. "slashing"
    extra_damage: str | None = None  # e.g. "1d6 fire"
    extra_damage_type: str | None = None
    save_ability: str | None = None  # e.g. "dex"
    save_dc: int | None = None
    half_on_save: bool = False


class MonsterAction(BaseModel):
    """One entry under the *Actions* heading of a stat block."""

    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    description: str = ""
    usage: ActionUsage = "at_will"
    recharge: str | None = None  # e.g. "5-6"
    uses_max: int | None = None
    uses_remaining: int | None = None
    attack: MonsterAttack | None = None


class MonsterReaction(BaseModel):
    """One entry under the *Reactions* heading."""

    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    description: str = ""
    trigger: str = ""


class LegendaryAction(BaseModel):
    """One legendary-action option on a stat block.

    ``cost`` is the number of legendary actions consumed (1–3). The
    per-round budget lives on :class:`CombatantState` so it can be
    decremented at runtime.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    description: str = ""
    cost: int = 1
    attack: MonsterAttack | None = None


# --------------------------------------------------------------------- #
# StatBlock
# --------------------------------------------------------------------- #


class StatBlock(BaseModel):
    """Canonical, immutable description of a monster / NPC.

    Hydrate a runtime combatant with :meth:`to_combatant`.
    """

    model_config = ConfigDict(extra="forbid")

    # Identity / classification
    key: str
    name: str
    size: str = "Medium"
    type: str = "humanoid"  # SRD creature type
    subtype: str | None = None
    alignment: str = "unaligned"

    # Vitals
    ac: int = 10
    hp: int = 1
    hit_dice: str | None = None  # e.g. "2d8+2"
    speed: int = 30
    speeds: dict[str, int] = Field(default_factory=dict)  # walk/fly/swim/climb/burrow

    # Abilities + saves
    abilities: dict[str, int] = Field(default_factory=dict)  # str/dex/.../cha → score
    saving_throws: dict[str, int] = Field(default_factory=dict)
    skills: dict[str, int] = Field(default_factory=dict)
    proficiency_bonus: int = 2

    # SRD damage-modifier vectors
    resistances: list[str] = Field(default_factory=list)
    vulnerabilities: list[str] = Field(default_factory=list)
    immunities: list[str] = Field(default_factory=list)
    condition_immunities: list[str] = Field(default_factory=list)

    # Senses / communication
    senses: Senses = Field(default_factory=Senses)
    languages: list[str] = Field(default_factory=list)

    # Threat
    challenge_rating: float | None = None
    xp: int | None = None

    # Behaviour
    traits: list[Trait] = Field(default_factory=list)
    actions: list[MonsterAction] = Field(default_factory=list)
    reactions: list[MonsterReaction] = Field(default_factory=list)
    legendary_actions: list[LegendaryAction] = Field(default_factory=list)
    legendary_actions_per_round: int | None = None

    # Versioning so future migrations can bump in place.
    schema_version: int = 1

    # ------------------------------------------------------------------ #
    # Derived
    # ------------------------------------------------------------------ #

    def derived_xp(self) -> int | None:
        """Return ``xp`` if set, otherwise look it up from CR."""
        if self.xp is not None:
            return self.xp
        return cr_to_xp(self.challenge_rating)

    # ------------------------------------------------------------------ #
    # Hydration
    # ------------------------------------------------------------------ #

    def to_combatant(
        self,
        *,
        actor_id: str,
        token_id: str | None = None,
        team: str = "foe",
        position: dict[str, Any] | None = None,
    ) -> "CombatantState":  # noqa: F821 (forward import)
        """Project this stat block into a fresh runtime combatant."""
        # Local import to avoid a module-level cycle.
        from ai_dm.game.combatant_state import CombatantState, Position

        return CombatantState(
            actor_id=actor_id,
            token_id=token_id,
            name=self.name,
            team=team,  # type: ignore[arg-type]
            controller="ai",
            source="npc",
            hp=self.hp,
            max_hp=self.hp,
            ac=self.ac,
            speed=self.speed,
            saving_throws=dict(self.saving_throws),
            resistances=list(self.resistances),
            vulnerabilities=list(self.vulnerabilities),
            immunities=list(self.immunities),
            condition_immunities=list(self.condition_immunities),
            senses=self.senses.model_copy(),
            languages=list(self.languages),
            challenge_rating=self.challenge_rating,
            xp=self.derived_xp(),
            traits=[t.model_copy() for t in self.traits],
            actions=[a.model_copy(deep=True) for a in self.actions],
            reactions=[r.model_copy() for r in self.reactions],
            legendary_actions=[la.model_copy(deep=True) for la in self.legendary_actions],
            legendary_actions_per_round=self.legendary_actions_per_round,
            legendary_actions_remaining=self.legendary_actions_per_round,
            stat_block_key=self.key,
            position=Position.model_validate(position) if isinstance(position, dict) else None,
        )


__all__ = [
    "ActionUsage",
    "CR_TO_XP",
    "LegendaryAction",
    "LegendaryUsage",
    "MonsterAction",
    "MonsterAttack",
    "MonsterReaction",
    "Senses",
    "StatBlock",
    "Trait",
    "cr_to_xp",
]

