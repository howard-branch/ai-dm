"""Canonical Python-side combatant state.

This module defines :class:`CombatantState`, the *golden source of truth*
for every combatant (PC or NPC) participating in — or eligible for —
combat. The Foundry side is treated strictly as a display surface;
all action-economy, resource and concentration bookkeeping happens
here.

Design notes
------------

* **Pydantic v2 models** with ``extra="forbid"`` everywhere, so any
  drift between this schema and on-disk JSON fails loudly during tests
  rather than silently in production.
* **Pure data + small mutation helpers.** The model owns its reset
  semantics (``start_of_turn`` / ``start_of_round`` / ``end_encounter``)
  but knows nothing about the event bus, the Foundry bridge or the
  rules engine. Callers wire those concerns.
* **Bridges from existing shapes.** :meth:`CombatantState.from_pc_sheet`
  consumes the dict produced by
  :func:`ai_dm.app.character_wizard.build_sheet` (and migrated by
  :func:`ai_dm.app.bootstrap._migrate_spell_block`).
  :meth:`CombatantState.from_npc_block` consumes a lightweight stat
  block — either inline NPC dicts or seed JSON under
  ``pack.paths.characters_seed``.
* **Schema versioning.** ``schema_version`` is stamped into every
  serialised record so a future ``_migrate_combatant`` shim (mirroring
  ``_migrate_spell_block``) can upgrade old saves in place.
"""
from __future__ import annotations

from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

# Re-export the existing Team alias so the rest of the engine has one
# canonical import path.
Team = Literal["party", "foe", "neutral"]
Controller = Literal["player", "ai", "gm"]
Source = Literal["pc", "npc"]
Recharge = Literal["short", "long", "turn", "round", "encounter", "never"]


# --------------------------------------------------------------------- #
# Sub-models
# --------------------------------------------------------------------- #


class Position(BaseModel):
    """Token position in scene coordinates.

    ``x``/``y`` are pixel coords (matching Foundry's ``move_token``
    payload shape). ``scene_id`` lets us round-trip cross-scene moves
    without ambiguity.
    """

    model_config = ConfigDict(extra="forbid")

    x: int = 0
    y: int = 0
    scene_id: str | None = None


class Concentration(BaseModel):
    """The single concentration spell a combatant is sustaining."""

    model_config = ConfigDict(extra="forbid")

    spell_id: str
    name: str | None = None
    target_ids: list[str] = Field(default_factory=list)
    save_dc: int | None = None
    started_round: int | None = None


class SpellSlot(BaseModel):
    """One spell-slot pool for a single level."""

    model_config = ConfigDict(extra="forbid")

    level: int
    current: int
    max: int

    def spend(self, n: int = 1) -> bool:
        if self.current < n:
            return False
        self.current -= n
        return True

    def restore(self, n: int | None = None) -> None:
        if n is None:
            self.current = self.max
        else:
            self.current = min(self.max, self.current + n)


class ResourceUse(BaseModel):
    """A generic per-feature resource pool (Second Wind, Channel Divinity, …)."""

    model_config = ConfigDict(extra="forbid")

    key: str
    name: str | None = None
    current: int = 0
    max: int = 0
    recharge: Recharge = "long"


# --------------------------------------------------------------------- #
# CombatantState
# --------------------------------------------------------------------- #


class CombatantState(BaseModel):
    """Canonical, authoritative state for one combatant.

    Every field needed to resolve a turn lives here; Foundry is a
    *projection target*, not a data source. Mutations should go through
    this object (or the helpers on it) so that any later sync to
    Foundry can simply diff the previous projection.
    """

    model_config = ConfigDict(extra="forbid")

    # --- identity --------------------------------------------------- #
    actor_id: str
    token_id: str | None = None
    name: str
    team: Team = "neutral"
    controller: Controller = "ai"
    source: Source = "pc"

    # --- vitals ----------------------------------------------------- #
    hp: int = 0
    max_hp: int = 0
    temp_hp: int = 0
    ac: int = 10

    # --- turn order ------------------------------------------------- #
    initiative: int | None = None
    initiative_bonus: int = 0

    # --- geometry --------------------------------------------------- #
    position: Position | None = None
    speed: int = 30

    # --- status ----------------------------------------------------- #
    conditions: list[str] = Field(default_factory=list)
    concentration: Concentration | None = None

    # --- SRD damage-modifier vectors -------------------------------- #
    resistances: list[str] = Field(default_factory=list)
    vulnerabilities: list[str] = Field(default_factory=list)
    immunities: list[str] = Field(default_factory=list)

    # --- SRD survival ----------------------------------------------- #
    exhaustion: int = 0
    death_saves: dict[str, Any] = Field(
        default_factory=lambda: {
            "successes": 0, "failures": 0, "stable": False, "dead": False,
        }
    )

    # --- SRD ability mods + saves (derived; cached for fast access) - #
    ability_mods: dict[str, int] = Field(default_factory=dict)
    saving_throws: dict[str, int] = Field(default_factory=dict)

    # --- resources -------------------------------------------------- #
    resources: dict[str, ResourceUse] = Field(default_factory=dict)
    spell_slots: dict[int, SpellSlot] = Field(default_factory=dict)

    # --- spells (ids only — the full sheet keeps full records) ----- #
    cantrips: list[str] = Field(default_factory=list)
    known_spells: list[str] = Field(default_factory=list)
    prepared_spells: list[str] = Field(default_factory=list)

    # --- action economy ------------------------------------------- #
    action_used: bool = False
    bonus_action_used: bool = False
    reaction_used: bool = False
    movement_used: int = 0

    # --- transient action effects (cleared at start of own turn) -- #
    # ``dashed``      → speed budget doubled for the turn.
    # ``dodging``     → attacks vs. you have disadvantage; dex saves
    #                   have advantage until start of your next turn.
    # ``disengaging`` → no opportunity attacks provoked this turn.
    # ``helping_target`` → actor_id you are helping (the target gets
    #                   advantage on its next attack/check).
    # ``readied_action`` → ``{"trigger": str, "action": str, "payload":
    #                   dict, "spell_level": int|None}`` reserving a
    #                   reaction. Concentration on a readied spell is
    #                   tracked separately on ``concentration``.
    # ``hidden``      → currently unseen by enemies; persists until
    #                   broken by attacking, casting, or being spotted,
    #                   so it is *not* cleared by ``start_of_turn``.
    dashed: bool = False
    dodging: bool = False
    disengaging: bool = False
    helping_target: str | None = None
    readied_action: dict[str, Any] | None = None
    hidden: bool = False

    # --- bookkeeping ----------------------------------------------- #
    schema_version: int = 2

    # ------------------------------------------------------------------ #
    # Reset semantics
    # ------------------------------------------------------------------ #

    def start_of_turn(self) -> None:
        """Called when this combatant becomes the active actor.

        Resets the per-turn slice of the action economy and any
        ``recharge="turn"`` resources. Reactions persist across turns
        (they reset at start of round).
        """
        self.action_used = False
        self.bonus_action_used = False
        self.movement_used = 0
        # Per-turn transient effects expire at the start of your next turn.
        self.dashed = False
        self.dodging = False
        self.disengaging = False
        self.helping_target = None
        self.readied_action = None
        self._restore_resources_with_recharge("turn")

    def start_of_round(self) -> None:
        """Called once per combat round, before the first actor.

        Resets reactions and any ``recharge="round"`` resources.
        """
        self.reaction_used = False
        self._restore_resources_with_recharge("round")

    def end_encounter(self) -> None:
        """Called when the encounter ends for any reason.

        Drops concentration and refreshes ``recharge="encounter"``
        resources. Long/short rest restoration is intentionally *not*
        handled here — that belongs to a future ``apply_rest`` helper
        invoked by the rest workflow.
        """
        self.concentration = None
        self.action_used = False
        self.bonus_action_used = False
        self.reaction_used = False
        self.movement_used = 0
        self.dashed = False
        self.dodging = False
        self.disengaging = False
        self.helping_target = None
        self.readied_action = None
        self.hidden = False
        self._restore_resources_with_recharge("encounter")

    def _restore_resources_with_recharge(self, kind: Recharge) -> None:
        for r in self.resources.values():
            if r.recharge == kind:
                r.current = r.max

    # ------------------------------------------------------------------ #
    # Convenience mutators
    # ------------------------------------------------------------------ #

    def take_damage(self, amount: int) -> int:
        """Apply ``amount`` damage, soaking temp HP first.

        Drops concentration when reduced to 0 HP. If the combatant is
        already at 0 HP, registers a death-save failure (per SRD).
        Returns the new HP.
        """
        if amount <= 0:
            return self.hp
        was_at_zero = self.hp == 0
        if self.temp_hp:
            absorbed = min(self.temp_hp, amount)
            self.temp_hp -= absorbed
            amount -= absorbed
        self.hp = max(0, self.hp - amount)
        if self.hp == 0 and self.concentration is not None:
            self.concentration = None
        if was_at_zero:
            track = self.death_saves or {}
            failures = int(track.get("failures", 0)) + 1
            track["failures"] = min(3, failures)
            if track["failures"] >= 3:
                track["dead"] = True
            self.death_saves = track
        return self.hp

    def heal(self, amount: int) -> int:
        if amount <= 0:
            return self.hp
        was_at_zero = self.hp == 0
        self.hp = min(self.max_hp, self.hp + amount)
        if was_at_zero and self.hp > 0:
            # Heal-from-0: clear death-save track and unconscious.
            self.death_saves = {
                "successes": 0, "failures": 0, "stable": False, "dead": False,
            }
            self.conditions = [c for c in self.conditions if c != "unconscious"]
        return self.hp

    def spend_slot(self, level: int, n: int = 1) -> bool:
        slot = self.spell_slots.get(level)
        if slot is None:
            return False
        return slot.spend(n)

    # ------------------------------------------------------------------ #
    # Builders
    # ------------------------------------------------------------------ #

    @classmethod
    def from_pc_sheet(
        cls,
        sheet: dict[str, Any],
        *,
        token_id: str | None = None,
        team: Team = "party",
        controller: Controller = "player",
    ) -> "CombatantState":
        """Project a v2 character sheet (post-``_migrate_spell_block``)
        into a fresh combatant.

        Robust to missing optional sections so it works equally well
        for hand-rolled fixture sheets.
        """
        hp_block = sheet.get("hp")
        if isinstance(hp_block, dict):
            hp = int(hp_block.get("current") or 0)
            max_hp = int(hp_block.get("max") or hp)
            temp_hp = int(hp_block.get("temp") or 0)
        else:
            hp = int(hp_block or 0)
            max_hp = int(sheet.get("max_hp") or hp)
            temp_hp = int(sheet.get("temp_hp") or 0)

        spells = sheet.get("spells") or {}
        return cls(
            actor_id=str(sheet.get("id") or sheet.get("actor_id") or ""),
            token_id=token_id or sheet.get("token_id"),
            name=str(sheet.get("name") or sheet.get("id") or "Unknown"),
            team=team,
            controller=controller,
            source="pc",
            hp=hp,
            max_hp=max_hp,
            temp_hp=temp_hp,
            ac=int(sheet.get("ac") or 10),
            speed=int(sheet.get("speed") or 30),
            conditions=list(sheet.get("conditions") or []),
            resistances=list(sheet.get("resistances") or []),
            vulnerabilities=list(sheet.get("vulnerabilities") or []),
            immunities=list(sheet.get("immunities") or []),
            exhaustion=int(sheet.get("exhaustion") or 0),
            death_saves=dict(sheet.get("death_saves") or {
                "successes": 0, "failures": 0, "stable": False, "dead": False,
            }),
            ability_mods=dict(sheet.get("ability_mods") or {}),
            saving_throws=dict(sheet.get("saving_throws") or {}),
            spell_slots=_slots_from_sheet(spells.get("slots")),
            cantrips=_spell_ids(spells.get("cantrips_known")),
            known_spells=_spell_ids(spells.get("known")),
            prepared_spells=_spell_ids(spells.get("prepared")),
            resources=_resources_from_features(sheet.get("abilities_features")),
        )

    @classmethod
    def from_npc_block(
        cls,
        block: dict[str, Any],
        *,
        token_id: str | None = None,
        team: Team = "foe",
    ) -> "CombatantState":
        """Project a lightweight NPC stat block.

        Accepts either flat ``{hp, max_hp, ac, ...}`` or a sheet-shaped
        ``hp: {current, max}`` block.
        """
        hp_block = block.get("hp")
        if isinstance(hp_block, dict):
            hp = int(hp_block.get("current") or hp_block.get("max") or 0)
            max_hp = int(hp_block.get("max") or hp)
        else:
            hp = int(hp_block or block.get("max_hp") or 0)
            max_hp = int(block.get("max_hp") or hp)
        position = block.get("position")
        return cls(
            actor_id=str(block.get("id") or block.get("actor_id") or ""),
            token_id=token_id or block.get("token_id"),
            name=str(block.get("name") or block.get("id") or "NPC"),
            team=team,
            controller="ai",
            source="npc",
            hp=hp,
            max_hp=max_hp,
            temp_hp=int(block.get("temp_hp") or 0),
            ac=int(block.get("ac") or 10),
            speed=int(block.get("speed") or 30),
            initiative_bonus=int(block.get("initiative_bonus") or 0),
            conditions=list(block.get("conditions") or []),
            resistances=list(block.get("resistances") or []),
            vulnerabilities=list(block.get("vulnerabilities") or []),
            immunities=list(block.get("immunities") or []),
            exhaustion=int(block.get("exhaustion") or 0),
            position=Position.model_validate(position) if isinstance(position, dict) else None,
        )


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #


def _spell_ids(records: Iterable[Any] | None) -> list[str]:
    """Extract ``id`` from a list of spell-record dicts (or pass-through ids)."""
    if not records:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for rec in records:
        if isinstance(rec, dict):
            sid = str(rec.get("id") or "").strip()
        else:
            sid = str(rec).strip()
        if sid and sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def _slots_from_sheet(slots: Any) -> dict[int, SpellSlot]:
    """Convert the on-disk ``{"1": {"max", "value"}, ...}`` shape to typed slots."""
    if not isinstance(slots, dict):
        return {}
    out: dict[int, SpellSlot] = {}
    for raw_lvl, body in slots.items():
        try:
            lvl = int(raw_lvl)
        except (TypeError, ValueError):
            continue
        if not isinstance(body, dict):
            continue
        mx = int(body.get("max") or 0)
        cur = int(body.get("value") if body.get("value") is not None else mx)
        out[lvl] = SpellSlot(level=lvl, current=cur, max=mx)
    return out


def _resources_from_features(features: Any) -> dict[str, ResourceUse]:
    """Pull per-feature ``uses`` blocks into the canonical resource map.

    Supports ``{"key": str, "uses": {"max": int, "recharge": str}}`` and
    bare ``{"name": str, "uses": int, "recharge": "short"}``. Unknown
    shapes are ignored so we degrade gracefully on hand-edited sheets.
    """
    if not isinstance(features, list):
        return {}
    out: dict[str, ResourceUse] = {}
    for feat in features:
        if not isinstance(feat, dict):
            continue
        uses = feat.get("uses")
        if uses is None:
            continue
        key = str(feat.get("key") or feat.get("id") or feat.get("name") or "").strip()
        if not key:
            continue
        if isinstance(uses, dict):
            mx = int(uses.get("max") or 0)
            recharge = str(uses.get("recharge") or "long")
        else:
            try:
                mx = int(uses)
            except (TypeError, ValueError):
                continue
            recharge = str(feat.get("recharge") or "long")
        if recharge not in ("short", "long", "turn", "round", "encounter", "never"):
            recharge = "long"
        out[key] = ResourceUse(
            key=key,
            name=str(feat.get("name") or key),
            current=mx,
            max=mx,
            recharge=recharge,  # type: ignore[arg-type]
        )
    return out


__all__ = [
    "CombatantState",
    "Concentration",
    "Controller",
    "Position",
    "Recharge",
    "ResourceUse",
    "Source",
    "SpellSlot",
    "Team",
]

