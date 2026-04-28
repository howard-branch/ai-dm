"""Unit tests for the per-actor Inventory model + EquipmentSlots."""
from __future__ import annotations

import pytest

from ai_dm.game.combatant_state import (
    CarriedItem, Coins, CombatantState, EquipmentSlots, Inventory,
)


def _fresh_inv() -> Inventory:
    return Inventory()


def test_default_inventory_is_empty():
    inv = _fresh_inv()
    assert inv.items == []
    assert inv.coins == Coins()
    assert inv.equipped == EquipmentSlots()
    assert inv.attuned == []


def test_combatant_has_inventory_and_schema_v3():
    c = CombatantState(actor_id="x", name="x")
    assert isinstance(c.inventory, Inventory)
    assert c.schema_version == 6


def test_give_stacks_consumables_and_creates_unique_ids():
    inv = _fresh_inv()
    a = inv.give("torch", 2)
    b = inv.give("torch", 3)
    assert a.instance_id == b.instance_id  # stacked
    assert a.qty == 5
    c = inv.give("potion_of_healing", 1)
    assert c.instance_id != a.instance_id


def test_drop_partial_and_full():
    inv = _fresh_inv()
    inv.give("torch", 5)
    dropped = inv.drop("torch#1", 2)
    assert dropped is not None and dropped.qty == 2
    assert inv.by_id("torch#1").qty == 3  # type: ignore[union-attr]
    full = inv.drop("torch#1")
    assert full is not None and full.qty == 3
    assert inv.by_id("torch#1") is None


def test_equip_main_hand_two_handed_clears_off_hand():
    inv = _fresh_inv()
    inv.give("greatsword", 1)
    inv.equip("greatsword#1", "main_hand", two_handed=True)
    assert inv.equipped.main_hand == "greatsword#1"
    assert inv.equipped.off_hand == "greatsword#1"
    assert inv.equipped.two_handed is True
    inv.unequip("main_hand")
    assert inv.equipped.main_hand is None
    assert inv.equipped.off_hand is None
    assert inv.equipped.two_handed is False


def test_equip_off_hand_breaks_two_handed_grip():
    inv = _fresh_inv()
    inv.give("greatsword", 1)
    inv.give("dagger", 1)
    inv.equip("greatsword#1", "main_hand", two_handed=True)
    inv.equip("dagger#1", "off_hand")
    assert inv.equipped.two_handed is False
    assert inv.equipped.off_hand == "dagger#1"


def test_attunement_capped_at_three():
    inv = _fresh_inv()
    for k in ("ring_a", "ring_b", "ring_c", "ring_d"):
        inv.give(k, 1)
    assert inv.attune("ring_a#1") is True
    assert inv.attune("ring_b#1") is True
    assert inv.attune("ring_c#1") is True
    # Fourth must fail per SRD.
    assert inv.attune("ring_d#1") is False
    assert inv.end_attunement("ring_a#1") is True
    assert inv.attune("ring_d#1") is True


def test_drop_clears_equipped_slot_automatically():
    inv = _fresh_inv()
    inv.give("longsword", 1)
    inv.equip("longsword#1", "main_hand")
    inv.drop("longsword#1")
    assert inv.equipped.main_hand is None


def test_spend_and_gain_currency():
    inv = _fresh_inv()
    inv.gain({"gp": 10})
    assert inv.coins.gp == 10
    ok = inv.spend({"gp": 5})
    assert ok and inv.coins == Coins(gp=5)
    assert inv.spend({"gp": 1000}) is False


def test_set_mastery_choice_validates_against_srd_keys():
    inv = _fresh_inv()
    inv.set_mastery_choice("longsword", "sap")
    assert inv.mastery_choices["longsword"] == "sap"
    with pytest.raises(ValueError):
        inv.set_mastery_choice("longsword", "rend")


def test_total_weight_includes_items_and_coins():
    inv = _fresh_inv()
    inv.give("longsword", 1)  # 3 lb
    inv.give("plate", 1)      # 65 lb
    inv.gain({"gp": 50})       # 50 coins / 50 = 1 lb
    assert inv.total_weight_lb() == 69.0


def test_inventory_serializes_and_round_trips():
    inv = _fresh_inv()
    inv.give("longsword", 1)
    inv.equip("longsword#1", "main_hand")
    inv.attune("longsword#1")
    inv.gain({"gp": 5})
    inv.set_mastery_choice("longsword", "sap")
    blob = inv.model_dump()
    parsed = Inventory.model_validate(blob)
    assert parsed == inv

