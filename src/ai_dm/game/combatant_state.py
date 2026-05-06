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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_dm.game.condition_instance import (
    ConcentrationLink,
    ConditionInstance,
    SaveToEnd,
    coerce_instance,
)
from ai_dm.game.monster_state import (
    LegendaryAction,
    MonsterAction,
    MonsterReaction,
    Senses,
    Trait,
)
from ai_dm.rules.rests import RestProgress

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


class Nourishment(BaseModel):
    """Per-actor food/water bookkeeping.

    Counters tick once per in-game day; the rules engine
    (:mod:`ai_dm.rules.sustenance`) decides when to translate them into
    exhaustion levels.
    """

    model_config = ConfigDict(extra="forbid")

    days_without_food: int = 0
    days_without_water: int = 0
    last_meal_minute: int | None = None
    last_drink_minute: int | None = None
    dehydration_failed_days: int = 0

    def eat_ration(self, *, at_minute: int | None = None) -> None:
        self.days_without_food = 0
        self.last_meal_minute = at_minute

    def drink_water(self, *, at_minute: int | None = None) -> None:
        self.days_without_water = 0
        self.dehydration_failed_days = 0
        self.last_drink_minute = at_minute

    def start_new_day(self, *, ate_today: bool, drank_today: bool) -> None:
        self.days_without_food = 0 if ate_today else self.days_without_food + 1
        self.days_without_water = 0 if drank_today else self.days_without_water + 1


# --------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------- #


SlotName = Literal["main_hand", "off_hand", "armor", "shield", "ammunition"]


class Coins(BaseModel):
    """Coin purse — 5 SRD denominations.

    Stored on every combatant; the heavy lifting (conversion, change,
    weight) lives in :mod:`ai_dm.rules.currency` so this remains a plain
    serialisable record.
    """

    model_config = ConfigDict(extra="forbid")

    cp: int = 0
    sp: int = 0
    ep: int = 0
    gp: int = 0
    pp: int = 0


class CarriedItem(BaseModel):
    """One stack of a single item key in an actor's inventory.

    ``instance_id`` is a stable per-stack id used by EquipmentSlots and
    by the equip/unequip/transfer payloads. Stacks of consumables share
    one ``CarriedItem`` (incrementing ``qty``); equipped or attuned
    items live in their own stack of ``qty=1`` so the references stay
    stable across mutations.
    """

    model_config = ConfigDict(extra="forbid")

    instance_id: str
    item_key: str
    qty: int = 1
    container: str | None = None  # instance_id of a container in items
    notes: str | None = None
    # Magic-item / +N hooks — left empty for SRD mundane items.
    magic_bonus: int = 0
    requires_attunement: bool = False
    # If set, points at a template in :mod:`ai_dm.rules.magic_items`.
    # Per-instance runtime fields (charges, attunement) live below.
    magic_item_key: str | None = None
    rarity: str | None = None
    consumable: bool = False
    charges: int | None = None
    max_charges: int | None = None
    armor_bonus: int = 0
    save_bonus: int = 0
    identified: bool = True


class EquipmentSlots(BaseModel):
    """The tangible 'in your hands / on your body' slots.

    Each value is the ``instance_id`` of a :class:`CarriedItem` (or
    ``None``). ``two_handed=True`` means the same instance occupies
    both ``main_hand`` and ``off_hand`` (e.g. a greatsword); the
    inventory helpers below enforce that invariant.
    """

    model_config = ConfigDict(extra="forbid")

    main_hand: str | None = None
    off_hand: str | None = None
    two_handed: bool = False
    armor: str | None = None
    shield: str | None = None
    ammunition: str | None = None


class Inventory(BaseModel):
    """Canonical per-combatant equipment + currency state.

    All bookkeeping the rules engine needs to compute AC, encumbrance,
    weapon damage, weapon mastery effects and shopping is right here.
    The serialised shape is forward-compatible: ``schema_version`` is
    bumped whenever a breaking field change ships, and
    :func:`ai_dm.app.bootstrap` is responsible for migrating older
    saves before they reach this model.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[CarriedItem] = Field(default_factory=list)
    equipped: EquipmentSlots = Field(default_factory=EquipmentSlots)
    attuned: list[str] = Field(default_factory=list)  # instance_ids; max 3
    coins: Coins = Field(default_factory=Coins)
    # Per-actor mastery pin: weapon_key → mastery slug they're using
    # this turn / encounter (the class' "available masteries" gate).
    mastery_choices: dict[str, str] = Field(default_factory=dict)
    schema_version: int = 1

    # ------------------------------------------------------------------ #
    # Lookups
    # ------------------------------------------------------------------ #

    def by_id(self, instance_id: str) -> CarriedItem | None:
        for it in self.items:
            if it.instance_id == instance_id:
                return it
        return None

    def by_key(self, item_key: str) -> list[CarriedItem]:
        return [it for it in self.items if it.item_key == item_key]

    def total_weight_lb(self) -> float:
        """Sum of item weight × qty + coin weight, in lb.

        Resolves item weights via :mod:`ai_dm.rules.equipment`; unknown
        slugs contribute 0 (so hand-edited saves don't crash).
        """
        from ai_dm.rules import currency, equipment as eq
        w = 0.0
        for it in self.items:
            w += eq.weight_of(it.item_key, it.qty)
        w += currency.weight(self.coins.model_dump())
        return round(w, 4)

    # ------------------------------------------------------------------ #
    # Mutations (return self for chaining)
    # ------------------------------------------------------------------ #

    def give(
        self,
        item_key: str,
        qty: int = 1,
        *,
        instance_id: str | None = None,
        container: str | None = None,
    ) -> CarriedItem:
        """Add ``qty`` of ``item_key``. Stacks with an existing carried
        copy of the same key when not equipped/attuned; otherwise creates
        a fresh stack. Returns the affected stack.

        Magic items (resolved via :func:`ai_dm.rules.magic_items.is_magic_item`)
        always create a fresh per-instance stack so per-instance state
        (charges, attunement, identification) remains independent.
        """
        from ai_dm.rules.magic_items import is_magic_item
        magical = is_magic_item(item_key)
        # Stack with first un-equipped, un-attuned, container-matching stack.
        equipped_ids = {
            self.equipped.main_hand, self.equipped.off_hand,
            self.equipped.armor, self.equipped.shield, self.equipped.ammunition,
        }
        if not magical:
            for it in self.items:
                if it.item_key != item_key:
                    continue
                if it.magic_item_key is not None:
                    continue
                if it.instance_id in equipped_ids or it.instance_id in self.attuned:
                    continue
                if it.container != container:
                    continue
                it.qty += int(qty)
                return it
        new_id = instance_id or _new_instance_id(item_key, self.items)
        stack = CarriedItem(instance_id=new_id, item_key=item_key, qty=int(qty), container=container)
        self.items.append(stack)
        return stack

    def give_magic_item(
        self,
        magic_item_key: str,
        *,
        qty: int = 1,
        base_item_key: str | None = None,
        instance_id: str | None = None,
        container: str | None = None,
        identified: bool = True,
    ) -> CarriedItem:
        """Instantiate a magic-item template into the inventory.

        ``magic_item_key`` must be a template defined in
        :mod:`ai_dm.rules.magic_items`. For ``base_item_required=True``
        templates (e.g. ``weapon_plus_1``) the caller must pass
        ``base_item_key`` (e.g. ``"longsword"``); the resulting
        :class:`CarriedItem` carries the *base* item key in
        ``item_key`` so weapon/armor catalogs keep working, and the
        template id in :attr:`magic_item_key`.

        Charges, rarity, attunement requirement, magic bonus and
        consumable flag are pre-populated from the template. Stacking
        is disabled — every magic item gets its own ``instance_id``.
        """
        from ai_dm.rules.magic_items import get_magic_item

        tpl = get_magic_item(magic_item_key)
        if tpl is None:
            raise ValueError(f"unknown magic item template {magic_item_key!r}")

        if tpl.base_item_required:
            if not base_item_key:
                raise ValueError(
                    f"magic item {magic_item_key!r} requires base_item_key"
                )
            inventory_key = base_item_key
        else:
            inventory_key = tpl.base_item or magic_item_key

        new_id = instance_id or _new_instance_id(inventory_key, self.items)
        stack = CarriedItem(
            instance_id=new_id,
            item_key=inventory_key,
            qty=max(1, int(qty)) if not tpl.consumable else int(qty),
            container=container,
            magic_item_key=magic_item_key,
            magic_bonus=tpl.weapon_bonus or 0,
            armor_bonus=tpl.armor_bonus or 0,
            save_bonus=tpl.save_bonus or 0,
            requires_attunement=tpl.requires_attunement,
            consumable=tpl.consumable,
            rarity=tpl.rarity,
            charges=tpl.max_charges,
            max_charges=tpl.max_charges,
            identified=identified,
        )
        self.items.append(stack)
        return stack

    def drop(self, instance_id: str, qty: int | None = None) -> CarriedItem | None:
        """Remove ``qty`` (or the whole stack) and return the dropped
        portion. Auto-unequips if the stack was held."""
        stack = self.by_id(instance_id)
        if stack is None:
            return None
        n = stack.qty if qty is None else min(int(qty), stack.qty)
        if n <= 0:
            return None
        # If we'd remove the entire stack, free any slots referencing it.
        if n == stack.qty:
            self._clear_slots_for(instance_id)
            if instance_id in self.attuned:
                self.attuned.remove(instance_id)
            self.items = [it for it in self.items if it.instance_id != instance_id]
            return stack.model_copy()
        stack.qty -= n
        return CarriedItem(
            instance_id=stack.instance_id,
            item_key=stack.item_key,
            qty=n,
            container=stack.container,
        )

    def equip(self, instance_id: str, slot: SlotName, *, two_handed: bool = False) -> None:
        """Place ``instance_id`` into ``slot``. Validates the item exists
        and that two-handed wielding clears the off-hand."""
        if self.by_id(instance_id) is None:
            raise ValueError(f"unknown instance_id {instance_id!r}")
        if slot == "main_hand":
            self.equipped.main_hand = instance_id
            self.equipped.two_handed = bool(two_handed)
            if two_handed:
                self.equipped.off_hand = instance_id
        elif slot == "off_hand":
            if self.equipped.two_handed:
                # main_hand was held two-handed; vacate that grip.
                self.equipped.two_handed = False
                if self.equipped.main_hand == self.equipped.off_hand:
                    # off_hand was a phantom of the two-handed grip; clear it cleanly
                    self.equipped.main_hand = None
            self.equipped.off_hand = instance_id
        elif slot == "armor":
            self.equipped.armor = instance_id
        elif slot == "shield":
            self.equipped.shield = instance_id
        elif slot == "ammunition":
            self.equipped.ammunition = instance_id
        else:  # pragma: no cover - typing makes this unreachable
            raise ValueError(f"unknown slot {slot!r}")

    def unequip(self, slot: SlotName) -> str | None:
        """Clear ``slot`` and return the instance_id that was there."""
        if slot == "main_hand":
            prev = self.equipped.main_hand
            self.equipped.main_hand = None
            if self.equipped.two_handed:
                self.equipped.off_hand = None
                self.equipped.two_handed = False
            return prev
        if slot == "off_hand":
            prev = self.equipped.off_hand
            self.equipped.off_hand = None
            self.equipped.two_handed = False
            return prev
        if slot == "armor":
            prev = self.equipped.armor
            self.equipped.armor = None
            return prev
        if slot == "shield":
            prev = self.equipped.shield
            self.equipped.shield = None
            return prev
        if slot == "ammunition":
            prev = self.equipped.ammunition
            self.equipped.ammunition = None
            return prev
        raise ValueError(f"unknown slot {slot!r}")  # pragma: no cover

    def attune(self, instance_id: str) -> bool:
        """Attune ``instance_id``. Returns ``False`` when at the SRD cap of 3."""
        from ai_dm.rules.attunement import MAX_ATTUNED

        if self.by_id(instance_id) is None:
            raise ValueError(f"unknown instance_id {instance_id!r}")
        if instance_id in self.attuned:
            return True
        if len(self.attuned) >= MAX_ATTUNED:
            return False
        self.attuned.append(instance_id)
        return True

    def end_attunement(self, instance_id: str) -> bool:
        if instance_id in self.attuned:
            self.attuned.remove(instance_id)
            return True
        return False

    # ------------------------------------------------------------------ #
    # Charges & consumables
    # ------------------------------------------------------------------ #

    def expend_charges(self, instance_id: str, n: int = 1) -> int:
        """Spend ``n`` charges from a charged magic item.

        Returns the number actually expended. Raises ``ValueError`` if
        the stack does not exist or has no charge pool. Refuses to
        overspend (returns 0). When the item is a consumable with a
        single use (``max_charges`` is None) and ``n>=1`` the stack's
        ``qty`` is decremented instead, removing it on hit-zero.
        """
        stack = self.by_id(instance_id)
        if stack is None:
            raise ValueError(f"unknown instance_id {instance_id!r}")
        if stack.max_charges is None:
            # Charge-less consumable (potion, scroll): "expending" means
            # using one of the stack's qty.
            if not stack.consumable:
                raise ValueError(
                    f"item {instance_id!r} has no charges and is not consumable"
                )
            return self._consume_one(stack, n=n)
        if n <= 0 or (stack.charges or 0) < n:
            return 0
        stack.charges = (stack.charges or 0) - n
        return n

    def restore_charges(
        self,
        instance_id: str,
        n: int | None = None,
    ) -> int:
        """Refill charges. ``n=None`` → top up to ``max_charges``.

        Returns the amount actually restored.
        """
        stack = self.by_id(instance_id)
        if stack is None or stack.max_charges is None:
            return 0
        cur = stack.charges or 0
        if n is None:
            restored = stack.max_charges - cur
        else:
            restored = max(0, min(stack.max_charges - cur, int(n)))
        stack.charges = cur + restored
        return restored

    def consume_one(self, instance_id: str) -> bool:
        """Use one unit of a consumable stack. Removes the stack on zero.

        Returns ``True`` when a unit was consumed, ``False`` when the
        item is not flagged consumable or the stack is missing.
        """
        stack = self.by_id(instance_id)
        if stack is None or not stack.consumable:
            return False
        return self._consume_one(stack, n=1) > 0

    def _consume_one(self, stack: CarriedItem, *, n: int = 1) -> int:
        if n <= 0:
            return 0
        used = min(n, stack.qty)
        stack.qty -= used
        if stack.qty <= 0:
            self._clear_slots_for(stack.instance_id)
            if stack.instance_id in self.attuned:
                self.attuned.remove(stack.instance_id)
            self.items = [it for it in self.items if it.instance_id != stack.instance_id]
        return used

    def dawn_recharge(self, roller: "DiceRoller | None" = None) -> dict[str, int]:
        """Apply per-dawn recharge to every charged magic item.

        Returns ``{instance_id: charges_restored}`` for inspection /
        narration. Items with a non-dawn recharge spec are skipped.
        """
        from ai_dm.rules.magic_items import (
            get_magic_item,
            roll_recharge_amount,
        )

        out: dict[str, int] = {}
        for stack in self.items:
            if stack.max_charges is None or stack.magic_item_key is None:
                continue
            tpl = get_magic_item(stack.magic_item_key)
            if tpl is None or tpl.recharge.per != "dawn":
                continue
            amt = roll_recharge_amount(
                tpl.recharge,
                roller=roller,
                max_charges=stack.max_charges,
            )
            out[stack.instance_id] = self.restore_charges(stack.instance_id, amt)
        return out

    # ------------------------------------------------------------------ #
    # Magic bonus / AC accessors
    # ------------------------------------------------------------------ #

    def equipped_weapon_bonus(self, slot: SlotName = "main_hand") -> int:
        """Return the +N magical attack/damage bonus of the equipped weapon."""
        inst_id = getattr(self.equipped, slot, None)
        if inst_id is None:
            return 0
        stack = self.by_id(inst_id)
        return int(stack.magic_bonus) if stack else 0

    def armor_bonus(self) -> int:
        """Return the +N magical AC contribution of equipped armor + shield."""
        bonus = 0
        for slot in ("armor", "shield"):
            inst_id = getattr(self.equipped, slot, None)
            if inst_id is None:
                continue
            stack = self.by_id(inst_id)
            if stack is not None:
                bonus += int(stack.armor_bonus)
        return bonus

    def attuned_passive_bonuses(self) -> dict[str, int]:
        """Sum passive AC / save bonuses granted by *attuned* magic items
        (Ring/Cloak of Protection, etc.).

        Only attuned items contribute — that's the SRD rule for these
        slot-less wonders. Returns ``{"ac": int, "saves": int}``.
        """
        ac = 0
        saves = 0
        for inst_id in self.attuned:
            stack = self.by_id(inst_id)
            if stack is None:
                continue
            # Don't double-count armor (its bonus is folded into armor_bonus()).
            if stack.instance_id in (self.equipped.armor, self.equipped.shield):
                continue
            ac += int(stack.armor_bonus)
            saves += int(stack.save_bonus)
        return {"ac": ac, "saves": saves}

    def spend(self, cost: Coins | dict[str, int]) -> bool:
        from ai_dm.rules import currency

        try:
            new_purse = currency.subtract(self.coins.model_dump(), cost)
        except currency.InsufficientFunds:
            return False
        self.coins = Coins(**new_purse.as_dict())
        return True

    def gain(self, amount: Coins | dict[str, int]) -> None:
        from ai_dm.rules import currency

        merged = currency.add(self.coins.model_dump(), amount)
        self.coins = Coins(**merged.as_dict())

    def set_mastery_choice(self, weapon_key: str, mastery: str) -> None:
        from ai_dm.rules.weapon_mastery import MASTERY_KEYS
        if mastery not in MASTERY_KEYS:
            raise ValueError(f"unknown weapon mastery {mastery!r}")
        self.mastery_choices[weapon_key] = mastery

    def clear_mastery_choice(self, weapon_key: str) -> None:
        self.mastery_choices.pop(weapon_key, None)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _clear_slots_for(self, instance_id: str) -> None:
        if self.equipped.main_hand == instance_id:
            self.equipped.main_hand = None
            if self.equipped.two_handed:
                self.equipped.off_hand = None
                self.equipped.two_handed = False
        if self.equipped.off_hand == instance_id:
            self.equipped.off_hand = None
            self.equipped.two_handed = False
        if self.equipped.armor == instance_id:
            self.equipped.armor = None
        if self.equipped.shield == instance_id:
            self.equipped.shield = None
        if self.equipped.ammunition == instance_id:
            self.equipped.ammunition = None


def _new_instance_id(item_key: str, existing: list[CarriedItem]) -> str:
    """Stable, human-readable instance id: ``<item_key>#<n>``."""
    n = 1
    used = {it.instance_id for it in existing}
    while True:
        cand = f"{item_key}#{n}"
        if cand not in used:
            return cand
        n += 1


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
    conditions: list[ConditionInstance] = Field(default_factory=list)
    concentration: Concentration | None = None

    # --- SRD damage-modifier vectors -------------------------------- #
    resistances: list[str] = Field(default_factory=list)
    vulnerabilities: list[str] = Field(default_factory=list)
    immunities: list[str] = Field(default_factory=list)
    condition_immunities: list[str] = Field(default_factory=list)

    # --- monster / NPC stat-block surface ---------------------------- #
    # Populated for ``source == "npc"`` combatants (PCs leave these
    # empty / null). The action / reaction / legendary lists are deep
    # copies of the immutable :class:`StatBlock` they were hydrated
    # from; mutation here (e.g. decrementing ``uses_remaining`` or
    # ``legendary_actions_remaining``) is the *runtime* slice.
    senses: Senses | None = None
    languages: list[str] = Field(default_factory=list)
    challenge_rating: float | None = None
    xp: int | None = None
    traits: list[Trait] = Field(default_factory=list)
    actions: list[MonsterAction] = Field(default_factory=list)
    reactions: list[MonsterReaction] = Field(default_factory=list)
    legendary_actions: list[LegendaryAction] = Field(default_factory=list)
    legendary_actions_per_round: int | None = None
    legendary_actions_remaining: int | None = None
    stat_block_key: str | None = None

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
    # Class- (for PCs) or CR- (for NPCs) derived proficiency bonus.
    # Default of +2 matches level 1 / CR ≤ 4 per SRD.
    proficiency_bonus: int = 2

    # --- resources -------------------------------------------------- #
    resources: dict[str, ResourceUse] = Field(default_factory=dict)
    spell_slots: dict[int, SpellSlot] = Field(default_factory=dict)

    # --- spells (ids only — the full sheet keeps full records) ----- #
    # ``cantrips``        → always-on level-0 spells (no slot cost).
    # ``known_spells``    → leveled spells the caster has *learned*.
    #                       For ``casting_style="known"`` casters
    #                       (sorcerer/bard/warlock family) this list
    #                       is what's castable.
    # ``prepared_spells`` → for ``casting_style="prepared"`` casters
    #                       (cleric/druid/wizard/paladin), the daily
    #                       subset chosen from ``known_spells`` /
    #                       spellbook that is castable today. Capped
    #                       by ``prepared_cap`` (= caster level + mod
    #                       per SRD).
    # ``rituals``         → spell ids the caster knows that are
    #                       castable as rituals (ritual-tag filtered);
    #                       wizards may ritual-cast anything in their
    #                       spellbook with the ritual tag, even when
    #                       not prepared. ``ritual_caster`` gates the
    #                       ability.
    cantrips: list[str] = Field(default_factory=list)
    known_spells: list[str] = Field(default_factory=list)
    prepared_spells: list[str] = Field(default_factory=list)
    rituals: list[str] = Field(default_factory=list)

    # --- caster derived stats --------------------------------------- #
    # Populated by the rules engine from class progression + ability
    # mods; left ``None`` for non-casters so callers can detect
    # "doesn't cast spells" cleanly.
    spellcasting_class: str | None = None
    spellcasting_ability: str | None = None  # "int" | "wis" | "cha"
    spell_attack_bonus: int | None = None
    spell_save_dc: int | None = None
    casting_style: Literal["known", "prepared"] | None = None
    prepared_cap: int | None = None
    ritual_caster: bool = False

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

    # --- equipment + currency ------------------------------------- #
    inventory: "Inventory" = Field(default_factory=lambda: Inventory())

    # --- exploration / survival ------------------------------------ #
    nourishment: Nourishment = Field(default_factory=Nourishment)
    rest_progress: RestProgress | None = None

    # --- bookkeeping ----------------------------------------------- #
    schema_version: int = 6

    # ------------------------------------------------------------------ #
    # Validators
    # ------------------------------------------------------------------ #

    @field_validator("conditions", mode="before")
    @classmethod
    def _coerce_conditions(cls, value: Any) -> list[ConditionInstance]:
        """Promote legacy ``list[str]`` (and dict) condition payloads.

        Sheets written under ``schema_version <= 4`` stored conditions
        as bare SRD keys; foundry payloads sometimes deliver them as
        plain dicts. Both are normalised into :class:`ConditionInstance`
        so downstream code only ever sees one shape. Unknown / empty
        entries are dropped silently.
        """
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            out: list[ConditionInstance] = []
            for item in value:
                inst = coerce_instance(item)
                if inst is not None:
                    out.append(inst)
            return out
        return value  # let pydantic raise the standard validation error

    # ------------------------------------------------------------------ #
    # Condition helpers (thin wrappers around ConditionTracker)
    # ------------------------------------------------------------------ #

    def condition_keys(self, *, include_implied: bool = True) -> set[str]:
        """Set of active SRD keys, optionally expanded with implications."""
        from ai_dm.rules.conditions import implied as _implied
        keys = {c.key for c in self.conditions}
        if self.exhaustion > 0:
            keys.add("exhaustion")
        return _implied(keys) if include_implied else keys

    def has_condition(self, key: str, *, include_implied: bool = True) -> bool:
        return key.strip().lower() in self.condition_keys(include_implied=include_implied)

    def add_condition(
        self,
        key: str,
        *,
        source: str = "unknown",
        duration_rounds: int | None = None,
        expires_at_round: int | None = None,
        expires_on: str = "never",
        save_to_end: SaveToEnd | dict | None = None,
        concentration_link: ConcentrationLink | dict | None = None,
        notes: str | None = None,
    ) -> ConditionInstance | None:
        """Apply ``key`` via :class:`ConditionTracker`.

        Returns the resulting instance, or ``None`` when blocked by a
        condition immunity.
        """
        from ai_dm.game.condition_tracker import ConditionTracker

        if isinstance(save_to_end, dict):
            save_to_end = SaveToEnd.model_validate(save_to_end)
        if isinstance(concentration_link, dict):
            concentration_link = ConcentrationLink.model_validate(concentration_link)
        inst = ConditionInstance(
            key=key,
            source=source,
            duration_rounds=duration_rounds,
            expires_at_round=expires_at_round,
            expires_on=expires_on,  # type: ignore[arg-type]
            save_to_end=save_to_end,
            concentration_link=concentration_link,
            notes=notes,
        )
        return ConditionTracker(self).apply(inst)

    def remove_condition(self, key: str, *, source: str | None = None) -> int:
        from ai_dm.game.condition_tracker import ConditionTracker
        return ConditionTracker(self).remove(key, source=source)

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
        # Legendary creatures regain their full per-round budget at the
        # top of the round (SRD: legendary actions reset on the owner's
        # turn — refreshing at start-of-round is functionally
        # equivalent for our event ordering and avoids an extra hook).
        if self.legendary_actions_per_round is not None:
            self.legendary_actions_remaining = self.legendary_actions_per_round

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

        Note: concentration *saves* on damage (the Con DC = max(10,
        floor(damage/2)) check) are rolled by the caller via
        :func:`ai_dm.rules.concentration.on_damage` — this method only
        handles the ``auto_drop_at_zero_hp`` case so it stays
        deterministic and roller-free.
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
            # Heal-from-0: clear death-save track and any HP-zero-sourced
            # unconscious. Other sources (e.g. Sleep spell) survive.
            self.death_saves = {
                "successes": 0, "failures": 0, "stable": False, "dead": False,
            }
            self.remove_condition("unconscious", source="hp_zero")
        return self.hp

    def spend_slot(self, level: int, n: int = 1) -> bool:
        slot = self.spell_slots.get(level)
        if slot is None:
            return False
        return slot.spend(n)

    def lowest_available_slot(self, min_level: int) -> int | None:
        """Return the smallest slot level ``>= min_level`` with a spend left.

        Useful when a caster wants to upcast at the cheapest viable
        slot. ``min_level`` must be >= 1 (cantrips don't consume slots).
        """
        if min_level < 1:
            return None
        for lvl in sorted(self.spell_slots):
            if lvl < min_level:
                continue
            if self.spell_slots[lvl].current > 0:
                return lvl
        return None

    def is_prepared(self, spell_id: str) -> bool:
        """Whether ``spell_id`` is castable right now (style-aware).

        * ``casting_style="known"`` → membership in ``known_spells`` /
          ``cantrips`` is sufficient.
        * ``casting_style="prepared"`` → must be in ``prepared_spells``
          (or ``cantrips``, which are always prepared).
        * ``None`` (legacy) → falls back to a permissive union.
        """
        if spell_id in self.cantrips:
            return True
        if self.casting_style == "prepared":
            return spell_id in self.prepared_spells
        if self.casting_style == "known":
            return spell_id in self.known_spells
        return spell_id in self.prepared_spells or spell_id in self.known_spells

    def can_ritual_cast(self, spell_id: str) -> bool:
        """Whether ``spell_id`` may be cast as a ritual (no slot)."""
        return self.ritual_caster and spell_id in self.rituals

    def spend_legendary(self, cost: int = 1) -> bool:
        """Consume ``cost`` legendary actions for this round.

        Returns ``False`` (without mutating) when the creature has no
        legendary economy or insufficient budget remaining.
        """
        if (
            self.legendary_actions_per_round is None
            or self.legendary_actions_remaining is None
        ):
            return False
        if cost <= 0 or self.legendary_actions_remaining < cost:
            return False
        self.legendary_actions_remaining -= cost
        return True

    def has_condition_immunity(self, condition: str) -> bool:
        """Membership test that's resilient to case + whitespace drift."""
        norm = condition.strip().lower()
        return any(c.strip().lower() == norm for c in self.condition_immunities)

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
            proficiency_bonus=int(sheet.get("proficiency_bonus") or 2),
            spell_slots=_slots_from_sheet(spells.get("slots")),
            cantrips=_spell_ids(spells.get("cantrips_known")),
            known_spells=_spell_ids(spells.get("known")),
            prepared_spells=_spell_ids(spells.get("prepared")),
            rituals=_spell_ids(spells.get("rituals")),
            spellcasting_class=_opt_str(spells.get("class") or sheet.get("class")),
            spellcasting_ability=_opt_str(spells.get("ability")),
            spell_attack_bonus=_opt_int(spells.get("attack_bonus")),
            spell_save_dc=_opt_int(spells.get("save_dc")),
            casting_style=_opt_casting_style(spells.get("casting_style")),
            prepared_cap=_opt_int(spells.get("prepared_cap")),
            ritual_caster=bool(spells.get("ritual_caster", False)),
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
        senses_raw = block.get("senses")
        senses_obj: Senses | None
        if isinstance(senses_raw, Senses):
            senses_obj = senses_raw
        elif isinstance(senses_raw, dict):
            senses_obj = Senses.model_validate(senses_raw)
        else:
            senses_obj = None

        def _coerce_list(items: Any, model: type[BaseModel]) -> list[Any]:
            if not items:
                return []
            out: list[Any] = []
            for it in items:
                if isinstance(it, model):
                    out.append(it.model_copy(deep=True))
                elif isinstance(it, dict):
                    out.append(model.model_validate(it))
            return out

        traits = _coerce_list(block.get("traits"), Trait)
        actions = _coerce_list(block.get("actions"), MonsterAction)
        reactions = _coerce_list(block.get("reactions"), MonsterReaction)
        legendary = _coerce_list(block.get("legendary_actions"), LegendaryAction)
        leg_per_round = block.get("legendary_actions_per_round")
        if leg_per_round is not None:
            leg_per_round = int(leg_per_round)
        leg_remaining = block.get("legendary_actions_remaining")
        if leg_remaining is None and leg_per_round is not None:
            leg_remaining = leg_per_round

        cr_raw = block.get("challenge_rating")
        try:
            cr_val = float(cr_raw) if cr_raw is not None else None
        except (TypeError, ValueError):
            cr_val = None

        xp_raw = block.get("xp")
        if xp_raw is None:
            from ai_dm.game.monster_state import cr_to_xp
            xp_val = cr_to_xp(cr_val)
        else:
            try:
                xp_val = int(xp_raw)
            except (TypeError, ValueError):
                xp_val = None

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
            condition_immunities=list(block.get("condition_immunities") or []),
            senses=senses_obj,
            languages=list(block.get("languages") or []),
            challenge_rating=cr_val,
            xp=xp_val,
            traits=traits,
            actions=actions,
            reactions=reactions,
            legendary_actions=legendary,
            legendary_actions_per_round=leg_per_round,
            legendary_actions_remaining=leg_remaining,
            stat_block_key=block.get("stat_block_key") or block.get("key"),
            saving_throws=dict(block.get("saving_throws") or {}),
            ability_mods=dict(block.get("ability_mods") or {}),
            proficiency_bonus=int(
                block.get("proficiency_bonus")
                or _cr_to_proficiency_bonus(cr_val)
            ),
            exhaustion=int(block.get("exhaustion") or 0),
            position=Position.model_validate(position) if isinstance(position, dict) else None,
        )


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _cr_to_proficiency_bonus(cr: float | None) -> int:
    """SRD prof-bonus table by challenge rating."""
    if cr is None:
        return 2
    if cr <= 4:
        return 2
    if cr <= 8:
        return 3
    if cr <= 12:
        return 4
    if cr <= 16:
        return 5
    if cr <= 20:
        return 6
    if cr <= 24:
        return 7
    if cr <= 28:
        return 8
    return 9


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_casting_style(value: Any) -> Literal["known", "prepared"] | None:
    s = _opt_str(value)
    if s in ("known", "prepared"):
        return s  # type: ignore[return-value]
    return None


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
    "CarriedItem",
    "Coins",
    "CombatantState",
    "ConcentrationLink",
    "Concentration",
    "ConditionInstance",
    "Controller",
    "EquipmentSlots",
    "Inventory",
    "LegendaryAction",
    "MonsterAction",
    "MonsterReaction",
    "Nourishment",
    "Position",
    "Recharge",
    "ResourceUse",
    "RestProgress",
    "SaveToEnd",
    "Senses",
    "SlotName",
    "Source",
    "SpellSlot",
    "Team",
    "Trait",
]


# Resolve forward references introduced by ``from __future__ import
# annotations`` so RestProgress / Inventory are wired into pydantic.
CombatantState.model_rebuild(_types_namespace={"RestProgress": RestProgress, "Inventory": Inventory})


