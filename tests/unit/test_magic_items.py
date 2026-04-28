"""Tests for the magic-item template catalog and per-instance state.

Covers:
- :mod:`ai_dm.rules.magic_items` template loader (rarity, attunement,
  charges, recharge, weapon/armor bonuses, consumables).
- :class:`ai_dm.game.combatant_state.Inventory` magic-item helpers
  (``give_magic_item``, ``expend_charges``, ``restore_charges``,
  ``consume_one``, ``dawn_recharge``) and the magical-bonus accessors.
"""
from __future__ import annotations

import pytest

from ai_dm.game.combatant_state import CombatantState, Inventory
from ai_dm.rules import magic_items as mi
from ai_dm.rules.dice import DiceRoller


# --------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------- #


class TestMagicItemCatalog:
    def test_known_items_are_loaded(self):
        assert mi.is_magic_item("potion_of_healing")
        assert mi.is_magic_item("ring_of_protection")
        assert mi.get_magic_item("does_not_exist") is None

    def test_potion_is_consumable(self):
        tpl = mi.get_magic_item("potion_of_healing")
        assert tpl is not None
        assert tpl.consumable is True
        assert tpl.requires_attunement is False
        assert tpl.rarity == "common"
        assert tpl.category == "potion"
        assert tpl.has_charges is False

    def test_weapon_template_has_bonus(self):
        tpl = mi.get_magic_item("weapon_plus_2")
        assert tpl is not None
        assert tpl.weapon_bonus == 2
        assert tpl.base_item_required is True

    def test_ring_of_protection_passive_bonuses(self):
        tpl = mi.get_magic_item("ring_of_protection")
        assert tpl is not None
        assert tpl.requires_attunement is True
        assert tpl.armor_bonus == 1
        assert tpl.save_bonus == 1
        assert tpl.is_passive_ac_bonus is True

    def test_wand_has_charges_and_dawn_recharge(self):
        tpl = mi.get_magic_item("wand_of_magic_missiles")
        assert tpl is not None
        assert tpl.max_charges == 7
        assert tpl.recharge.per == "dawn"
        assert tpl.recharge.dice == "1d6+1"
        assert tpl.has_charges is True

    def test_rarities_and_categories_exposed(self):
        assert "rare" in mi.RARITIES
        assert "potion" in mi.CATEGORIES


# --------------------------------------------------------------------- #
# Recharge resolver
# --------------------------------------------------------------------- #


class TestRollRechargeAmount:
    def test_no_dice_full_restore(self):
        spec = mi.RechargeSpec(per="dawn")
        assert mi.roll_recharge_amount(spec, max_charges=7) == 7

    def test_seeded_roller_is_deterministic(self):
        spec = mi.RechargeSpec(per="dawn", dice="1d6+1")
        a = mi.roll_recharge_amount(spec, roller=DiceRoller(seed=42))
        b = mi.roll_recharge_amount(spec, roller=DiceRoller(seed=42))
        assert a == b
        assert 2 <= a <= 7


# --------------------------------------------------------------------- #
# give_magic_item
# --------------------------------------------------------------------- #


class TestGiveMagicItem:
    def test_potion_populates_template_fields(self):
        inv = Inventory()
        stack = inv.give_magic_item("potion_of_healing")
        assert stack.magic_item_key == "potion_of_healing"
        assert stack.consumable is True
        assert stack.rarity == "common"
        assert stack.requires_attunement is False
        assert stack.charges is None
        assert stack.max_charges is None

    def test_wand_starts_at_max_charges(self):
        inv = Inventory()
        stack = inv.give_magic_item("wand_of_magic_missiles")
        assert stack.charges == 7
        assert stack.max_charges == 7

    def test_plus_one_weapon_requires_base_key(self):
        inv = Inventory()
        with pytest.raises(ValueError):
            inv.give_magic_item("weapon_plus_1")
        stack = inv.give_magic_item("weapon_plus_1", base_item_key="longsword")
        assert stack.item_key == "longsword"
        assert stack.magic_item_key == "weapon_plus_1"
        assert stack.magic_bonus == 1

    def test_unknown_template_raises(self):
        inv = Inventory()
        with pytest.raises(ValueError):
            inv.give_magic_item("nope")

    def test_magic_items_never_stack(self):
        inv = Inventory()
        a = inv.give_magic_item("potion_of_healing")
        b = inv.give_magic_item("potion_of_healing")
        assert a.instance_id != b.instance_id
        assert len([s for s in inv.items if s.magic_item_key == "potion_of_healing"]) == 2

    def test_mundane_give_still_stacks(self):
        inv = Inventory()
        a = inv.give("torch", 2)
        b = inv.give("torch", 1)
        assert a.instance_id == b.instance_id
        assert a.qty == 3


# --------------------------------------------------------------------- #
# Charges & consumables
# --------------------------------------------------------------------- #


class TestChargesAndConsumables:
    def test_expend_charges_decrements(self):
        inv = Inventory()
        wand = inv.give_magic_item("wand_of_magic_missiles")
        spent = inv.expend_charges(wand.instance_id, 3)
        assert spent == 3
        assert inv.by_id(wand.instance_id).charges == 4  # type: ignore[union-attr]

    def test_expend_charges_overspend_returns_zero(self):
        inv = Inventory()
        wand = inv.give_magic_item("wand_of_magic_missiles")
        assert inv.expend_charges(wand.instance_id, 99) == 0
        assert inv.by_id(wand.instance_id).charges == 7  # unchanged  # type: ignore[union-attr]

    def test_restore_charges_full_and_partial(self):
        inv = Inventory()
        wand = inv.give_magic_item("wand_of_magic_missiles")
        inv.expend_charges(wand.instance_id, 5)  # 7 → 2
        assert inv.restore_charges(wand.instance_id, 1) == 1
        assert inv.by_id(wand.instance_id).charges == 3  # type: ignore[union-attr]
        assert inv.restore_charges(wand.instance_id) == 4  # back to max
        assert inv.by_id(wand.instance_id).charges == 7  # type: ignore[union-attr]

    def test_consume_one_decrements_qty_and_removes_at_zero(self):
        inv = Inventory()
        potion = inv.give_magic_item("potion_of_healing")
        # Bump qty to 2 by adding another.
        potion2 = inv.give_magic_item("potion_of_healing")
        assert inv.consume_one(potion.instance_id) is True
        assert inv.by_id(potion.instance_id) is None  # qty was 1, gone
        assert inv.consume_one(potion2.instance_id) is True
        assert inv.by_id(potion2.instance_id) is None

    def test_consume_one_rejects_non_consumable(self):
        inv = Inventory()
        wand = inv.give_magic_item("wand_of_magic_missiles")
        assert inv.consume_one(wand.instance_id) is False

    def test_expend_charges_unknown_raises(self):
        inv = Inventory()
        with pytest.raises(ValueError):
            inv.expend_charges("nothing#1", 1)

    def test_dawn_recharge_only_dawn_items(self):
        inv = Inventory()
        wand = inv.give_magic_item("wand_of_magic_missiles")
        inv.expend_charges(wand.instance_id, 7)  # drain
        # Boots of speed have no charges → must be skipped, not crash.
        inv.give_magic_item("boots_of_speed")
        result = inv.dawn_recharge(roller=DiceRoller(seed=1))
        assert wand.instance_id in result
        cur = inv.by_id(wand.instance_id).charges  # type: ignore[union-attr]
        assert cur is not None and 1 <= cur <= 7


# --------------------------------------------------------------------- #
# Bonuses
# --------------------------------------------------------------------- #


class TestMagicalBonuses:
    def test_equipped_weapon_bonus(self):
        inv = Inventory()
        sword = inv.give_magic_item(
            "weapon_plus_2", base_item_key="longsword"
        )
        assert inv.equipped_weapon_bonus("main_hand") == 0  # not equipped yet
        inv.equip(sword.instance_id, "main_hand")
        assert inv.equipped_weapon_bonus("main_hand") == 2

    def test_armor_bonus_sums_armor_and_shield(self):
        inv = Inventory()
        plate = inv.give_magic_item("armor_plus_1", base_item_key="plate")
        shield = inv.give_magic_item("shield_plus_1")
        inv.equip(plate.instance_id, "armor")
        inv.equip(shield.instance_id, "shield")
        assert inv.armor_bonus() == 2

    def test_attuned_passive_bonus_only_when_attuned(self):
        inv = Inventory()
        ring = inv.give_magic_item("ring_of_protection")
        # Not yet attuned → no passive contribution.
        assert inv.attuned_passive_bonuses() == {"ac": 0, "saves": 0}
        inv.attune(ring.instance_id)
        assert inv.attuned_passive_bonuses() == {"ac": 1, "saves": 1}

    def test_attuned_armor_does_not_double_count(self):
        inv = Inventory()
        plate = inv.give_magic_item("armor_plus_1", base_item_key="plate")
        inv.equip(plate.instance_id, "armor")
        inv.attune(plate.instance_id)
        # armor_bonus() counts equipped; passives must skip equipped armor.
        assert inv.armor_bonus() == 1
        assert inv.attuned_passive_bonuses()["ac"] == 0


# --------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------- #


def test_round_trip_preserves_magic_state():
    inv = Inventory()
    wand = inv.give_magic_item("wand_of_magic_missiles")
    inv.expend_charges(wand.instance_id, 2)
    blob = inv.model_dump()
    parsed = Inventory.model_validate(blob)
    assert parsed == inv
    assert parsed.by_id(wand.instance_id).charges == 5  # type: ignore[union-attr]


def test_combatant_inventory_carries_magic_items():
    c = CombatantState(actor_id="a", name="A")
    c.inventory.give_magic_item("potion_of_healing")
    blob = c.model_dump()
    again = CombatantState.model_validate(blob)
    assert again.inventory.items[0].magic_item_key == "potion_of_healing"
    assert again.inventory.items[0].consumable is True

