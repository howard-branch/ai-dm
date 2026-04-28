"""SRD 5.2 magic-item template catalog.

A *magic item template* is the immutable definition of a magic item
(rarity, attunement, charges, recharge schedule, weapon/armor bonus,
consumable flag, …). Per-actor *instances* live on
:class:`ai_dm.game.combatant_state.CarriedItem` and reference the
template via :pyattr:`CarriedItem.magic_item_key`.

Single source of truth: ``assets/srd5_2/core/magic_items.json``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ai_dm.rules.dice import DiceRoller
from ai_dm.rules.srd_core import load

Rarity = Literal[
    "common", "uncommon", "rare", "very_rare", "legendary", "artifact"
]
ItemCategory = Literal[
    "weapon", "armor", "shield", "wondrous", "ring", "rod", "staff",
    "wand", "potion", "scroll",
]


@dataclass(frozen=True)
class RechargeSpec:
    """How a charged item refills.

    ``per`` is the trigger ("dawn", "dusk", "short_rest", "long_rest",
    or "never"). ``dice`` is an optional dice expression rolled to
    determine how many charges return; when omitted, all charges return.
    ``destroy_on_zero_dc`` is the DC of the d20 the DM rolls when an
    item is reduced to 0 charges (e.g. a wand "crumbles to dust on a
    1"); ``None`` means the item never self-destructs.
    """

    per: Literal["never", "dawn", "dusk", "short_rest", "long_rest"] = "never"
    dice: str | None = None
    destroy_on_zero_dc: int | None = None


@dataclass(frozen=True)
class MagicItem:
    key: str
    name: str
    category: ItemCategory
    rarity: Rarity
    requires_attunement: bool = False
    attunement_classes: tuple[str, ...] = ()
    consumable: bool = False
    weapon_bonus: int = 0
    armor_bonus: int = 0
    save_bonus: int = 0
    max_charges: int | None = None
    recharge: RechargeSpec = field(default_factory=RechargeSpec)
    weight: float = 0.0
    cost_amount: float = 0.0
    cost_unit: str = "gp"
    base_item: str | None = None
    base_item_required: bool = False
    container_capacity_lb: float | None = None
    properties: tuple[str, ...] = ()
    description: str = ""

    # ------------------------------------------------------------------ #
    # Convenience predicates
    # ------------------------------------------------------------------ #

    @property
    def has_charges(self) -> bool:
        return self.max_charges is not None and self.max_charges > 0

    @property
    def is_passive_ac_bonus(self) -> bool:
        """Items granting an always-on AC bonus while attuned."""
        return self.armor_bonus > 0 and self.category in ("ring", "wondrous")


# --------------------------------------------------------------------- #
# Loader
# --------------------------------------------------------------------- #


def _from_record(rec: dict[str, Any]) -> MagicItem:
    cost = rec.get("cost") or {}
    rch = rec.get("recharge") or {}
    return MagicItem(
        key=str(rec["key"]),
        name=str(rec["name"]),
        category=rec["category"],
        rarity=rec["rarity"],
        requires_attunement=bool(rec.get("requires_attunement", False)),
        attunement_classes=tuple(rec.get("attunement_classes") or ()),
        consumable=bool(rec.get("consumable", False)),
        weapon_bonus=int(rec.get("weapon_bonus", 0) or 0),
        armor_bonus=int(rec.get("armor_bonus", 0) or 0),
        save_bonus=int(rec.get("save_bonus", 0) or 0),
        max_charges=(
            int(rec["max_charges"])
            if rec.get("max_charges") is not None
            else None
        ),
        recharge=RechargeSpec(
            per=rch.get("per", "never"),
            dice=rch.get("dice"),
            destroy_on_zero_dc=rch.get("destroy_on_zero_dc"),
        ),
        weight=float(rec.get("weight", 0) or 0),
        cost_amount=float(cost.get("amount", 0) or 0),
        cost_unit=str(cost.get("unit", "gp")),
        base_item=rec.get("base_item"),
        base_item_required=bool(rec.get("base_item_required", False)),
        container_capacity_lb=rec.get("container_capacity_lb"),
        properties=tuple(rec.get("properties") or ()),
        description=str(rec.get("description") or ""),
    )


_DATA = load("magic_items")
_BY_KEY: dict[str, MagicItem] = {
    rec["key"]: _from_record(rec) for rec in _DATA["items"]
}

RARITIES: tuple[str, ...] = tuple(_DATA.get("rarities") or ())
CATEGORIES: tuple[str, ...] = tuple(_DATA.get("categories") or ())


def get_magic_item(key: str) -> MagicItem | None:
    return _BY_KEY.get(key)


def is_magic_item(key: str) -> bool:
    return key in _BY_KEY


def all_magic_items() -> list[MagicItem]:
    return list(_BY_KEY.values())


# --------------------------------------------------------------------- #
# Recharge helper
# --------------------------------------------------------------------- #


def roll_recharge_amount(
    spec: RechargeSpec,
    *,
    roller: DiceRoller | None = None,
    max_charges: int | None = None,
) -> int:
    """Resolve ``spec.dice`` into an integer charge restoration amount.

    * No dice expression → restore all (``max_charges`` if known, else
      a sentinel large value the caller should clamp).
    * No roller → return the *expected average* (round to nearest int).
    * Otherwise → roll the dice and return the total.
    """
    if spec.dice is None:
        return max_charges if max_charges is not None else 1_000_000
    if roller is None:
        # Cheap deterministic fallback: average of the expression.
        from ai_dm.rules.dice import DiceRoller as _DR
        avg_roller = _DR(seed=0)
        return avg_roller.roll(spec.dice).total
    return roller.roll(spec.dice).total


__all__ = [
    "CATEGORIES",
    "ItemCategory",
    "MagicItem",
    "RARITIES",
    "Rarity",
    "RechargeSpec",
    "all_magic_items",
    "get_magic_item",
    "is_magic_item",
    "roll_recharge_amount",
]

