"""Unit tests for the SRD weapons catalog (assets/srd5_2/core/weapons.json)."""
from __future__ import annotations

from ai_dm.rules import weapons


def test_longsword_versatile_and_mastery():
    w = weapons.get_weapon("longsword")
    assert w is not None
    assert weapons.damage_for(w) == ("1d8", "slashing")
    assert weapons.damage_for(w, two_handed=True) == ("1d10", "slashing")
    assert w.mastery == "sap"
    assert weapons.is_versatile(w)
    assert not weapons.is_two_handed(w)


def test_rapier_finesse_vex():
    w = weapons.get_weapon("rapier")
    assert w is not None
    assert weapons.is_finesse(w)
    assert w.mastery == "vex"


def test_longbow_range_and_slow():
    w = weapons.get_weapon("longbow")
    assert w is not None
    assert weapons.attack_range(w) == (150, 600)
    assert weapons.is_ranged(w)
    assert w.mastery == "slow"


def test_greatsword_two_handed_2d6():
    w = weapons.get_weapon("greatsword")
    assert w is not None
    assert w.damage.dice == "2d6"
    assert weapons.is_two_handed(w)
    assert w.mastery == "graze"


def test_thrown_dagger_has_range():
    w = weapons.get_weapon("dagger")
    assert w is not None
    assert weapons.is_thrown(w)
    assert weapons.attack_range(w) == (20, 60)


def test_unknown_weapon_returns_none():
    assert weapons.get_weapon("vorpal_runesword") is None


def test_glaive_reach():
    w = weapons.get_weapon("glaive")
    assert w is not None
    assert weapons.has_reach(w)
    # Pure-melee reach weapon: attack_range falls back to (10, 10)
    assert weapons.attack_range(w) == (10, 10)

