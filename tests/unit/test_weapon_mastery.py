"""Unit tests for 2024 SRD weapon mastery dispatch."""
from __future__ import annotations

from ai_dm.rules import weapon_mastery as wm
from ai_dm.rules import weapons


def test_eight_canonical_masteries():
    assert wm.MASTERY_KEYS == frozenset({
        "cleave", "graze", "nick", "push", "sap", "slow", "topple", "vex",
    })


def test_class_progression_fighter_advances():
    assert wm.mastery_count_for("fighter", 1) == 3
    assert wm.mastery_count_for("fighter", 4) == 4
    assert wm.mastery_count_for("fighter", 10) == 5
    assert wm.mastery_count_for("fighter", 16) == 6
    # Unknown class → 0.
    assert wm.mastery_count_for("wizard", 20) == 0
    # Below first level threshold → 0.
    assert wm.mastery_count_for("rogue", 0) == 0


def test_push_emits_10ft_push():
    longbow = weapons.get_weapon("heavy_crossbow")
    assert longbow is not None
    out = wm.apply_mastery("push", weapon=None, target={"actor_id": "ogre"}, hit=True, damage=8)
    assert out["key"] == "push"
    assert out["effects"][0] == {"kind": "push", "target_id": "ogre", "distance_ft": 10, "max_size": "large"}


def test_topple_emits_constitution_save():
    out = wm.apply_mastery("topple", target={"actor_id": "ogre"}, hit=True, damage=12,
                           proficiency_bonus=3, attack_ability_mod=4)
    eff = out["effects"][0]
    assert eff["kind"] == "request_save"
    assert eff["ability"] == "con"
    assert eff["dc"] == 8 + 4 + 3
    assert eff["on_fail"] == "prone"


def test_graze_only_on_miss_and_uses_ability_mod():
    weapon = {"damage": {"type": "slashing", "dice": "1d10"}}
    miss = wm.apply_mastery("graze", weapon=weapon, target={"actor_id": "x"},
                            hit=False, attack_ability_mod=3)
    assert miss["effects"][0] == {"kind": "graze_damage", "amount": 3,
                                  "target_id": "x", "damage_type": "slashing"}
    hit = wm.apply_mastery("graze", weapon=weapon, target={"actor_id": "x"},
                           hit=True, attack_ability_mod=3)
    assert hit["effects"] == []


def test_cleave_only_on_hit_with_heavy():
    light = {"properties": ["light"], "damage": {"dice": "1d6", "type": "slashing"}}
    heavy = {"properties": ["heavy", "two_handed"], "damage": {"dice": "1d12", "type": "slashing"}}
    assert wm.apply_mastery("cleave", weapon=light, hit=True, damage=5)["effects"] == []
    eff = wm.apply_mastery("cleave", weapon=heavy, hit=True, damage=10)["effects"][0]
    assert eff == {"kind": "cleave", "weapon_die": "1d12", "damage_type": "slashing"}


def test_unknown_mastery_returns_empty():
    assert wm.apply_mastery("rend", hit=True) == {"key": None, "effects": []}
    assert wm.apply_mastery(None, hit=True) == {"key": None, "effects": []}


def test_vex_requires_damage():
    assert wm.apply_mastery("vex", target={"actor_id": "g"}, hit=True, damage=0)["effects"] == []
    eff = wm.apply_mastery("vex", target={"actor_id": "g"}, hit=True, damage=4)["effects"][0]
    assert eff == {"kind": "advantage_on_next_attack", "target_id": "g"}

