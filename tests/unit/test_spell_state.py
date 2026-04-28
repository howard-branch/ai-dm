"""Unit tests for ai_dm.game.spell_state magic-rules models."""
from __future__ import annotations

from ai_dm.game.spell_state import (
    ClassSpellList,
    Spell,
    SpellAttack,
    SpellComponents,
    SpellDamage,
    SpellDuration,
    SpellSave,
    SpellScaling,
    build_class_spell_lists,
    spell_attack_bonus,
    spell_save_dc,
)


def test_components_infer_m_when_material_given() -> None:
    c = SpellComponents(v=True, material="a diamond worth 300 gp", material_cost_gp=300)
    assert c.m is True
    assert c.material_cost_gp == 300


def test_concentration_and_ritual_flags() -> None:
    spell = Spell(
        id="bless",
        name="Bless",
        level=1,
        school="enchantment",
        classes=["cleric", "paladin"],
        casting_time={"unit": "action", "ritual": False},
        duration={"kind": "minutes", "amount": 1, "concentration": True},
    )
    assert spell.requires_concentration is True
    assert spell.is_ritual is False
    assert spell.is_cantrip is False


def test_costly_material_predicate() -> None:
    revivify = Spell(
        id="revivify",
        name="Revivify",
        level=3,
        components={
            "v": True, "s": True, "m": True,
            "material": "diamonds worth 300+ gp",
            "material_cost_gp": 300,
            "material_consumed": True,
        },
    )
    assert revivify.has_costly_material is True
    assert revivify.components.material_consumed is True


def test_upcast_dice_scaling() -> None:
    fireball = Spell(
        id="fireball",
        name="Fireball",
        level=3,
        damage=SpellDamage(dice="8d6", type="fire"),
        scaling=SpellScaling(
            higher_level_dice_per_slot_above="1d6",
            higher_level_base_level=3,
        ),
    )
    assert fireball.upcast_dice(3) == "8d6"
    assert fireball.upcast_dice(5) == "10d6"
    assert fireball.upcast_dice(9) == "14d6"
    assert fireball.upcast_dice(2) is None  # below base


def test_cantrip_scaling_default_thresholds() -> None:
    fire_bolt = Spell(
        id="fire_bolt",
        name="Fire Bolt",
        level=0,
        damage=SpellDamage(dice="1d10", type="fire"),
    )
    assert fire_bolt.cantrip_dice_at_caster_level(1) == "1d10"
    assert fire_bolt.cantrip_dice_at_caster_level(5) == "2d10"
    assert fire_bolt.cantrip_dice_at_caster_level(11) == "3d10"
    assert fire_bolt.cantrip_dice_at_caster_level(20) == "4d10"


def test_slot_levels_castable_at() -> None:
    s = Spell(id="s", name="S", level=2)
    assert s.slot_levels_castable_at() == [2, 3, 4, 5, 6, 7, 8, 9]
    cantrip = Spell(id="c", name="C", level=0)
    assert cantrip.slot_levels_castable_at() == [0]


def test_attack_and_save_blocks_are_optional_and_independent() -> None:
    s = Spell(
        id="hold_person",
        name="Hold Person",
        level=2,
        save=SpellSave(ability="wis", no_effect_on_save=True),
    )
    assert s.attack is None
    assert s.save is not None and s.save.ability == "wis"
    s2 = Spell(id="fb", name="Fire Bolt", level=0,
               attack=SpellAttack(kind="spell_attack", range="ranged"))
    assert s2.save is None
    assert s2.attack is not None


def test_is_available_to_class_or_archetype() -> None:
    s = Spell(id="s", name="S", level=1, classes=["wizard"])
    assert s.is_available_to(class_key="wizard") is True
    assert s.is_available_to(class_key="cleric") is False
    open_spell = Spell(id="o", name="O", level=1)
    assert open_spell.is_available_to(class_key="anyone") is True


def test_build_class_spell_lists_groups_by_class_and_level() -> None:
    catalog = {
        "magic_missile": {"id": "magic_missile", "name": "Magic Missile",
                           "level": 1, "classes": ["wizard", "sorcerer"]},
        "cure_wounds": {"id": "cure_wounds", "name": "Cure Wounds",
                         "level": 1, "classes": ["cleric", "druid"]},
        "fireball": {"id": "fireball", "name": "Fireball",
                      "level": 3, "classes": ["wizard", "sorcerer"]},
    }
    lists = build_class_spell_lists(catalog)
    assert isinstance(lists["wizard"], ClassSpellList)
    assert lists["wizard"].at_level(1) == ["magic_missile"]
    assert lists["wizard"].at_level(3) == ["fireball"]
    assert lists["cleric"].at_level(1) == ["cure_wounds"]
    assert lists["sorcerer"].all_ids() == ["magic_missile", "fireball"]


def test_caster_derivation_helpers() -> None:
    assert spell_save_dc(ability_mod=4, proficiency_bonus=3) == 15
    assert spell_attack_bonus(ability_mod=4, proficiency_bonus=3) == 7


def test_duration_concentration_default_false() -> None:
    d = SpellDuration(kind="instantaneous")
    assert d.concentration is False

