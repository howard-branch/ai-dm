"""Unit tests for the SRD armor catalog and AC math."""
from __future__ import annotations

import pytest

from ai_dm.rules import armor


def test_plate_ac_and_strength_requirement():
    plate = armor.get_armor("plate")
    assert plate is not None
    # Heavy armor ignores Dex.
    assert armor.compute_ac(plate, dex_mod=0) == 18
    assert armor.compute_ac(plate, dex_mod=4) == 18
    assert plate.strength_req == 15
    assert not armor.meets_strength_requirement(plate, 14)
    assert armor.meets_strength_requirement(plate, 15)
    assert armor.imposes_stealth_disadvantage(plate)


def test_half_plate_dex_capped_at_2():
    hp = armor.get_armor("half_plate")
    assert hp is not None
    assert armor.compute_ac(hp, dex_mod=4) == 17  # 15 + min(4, 2)
    assert armor.compute_ac(hp, dex_mod=1) == 16
    assert armor.imposes_stealth_disadvantage(hp)


def test_leather_full_dex_no_stealth_penalty():
    lt = armor.get_armor("leather")
    assert lt is not None
    assert armor.compute_ac(lt, dex_mod=4) == 15  # 11 + 4
    assert not armor.imposes_stealth_disadvantage(lt)


def test_unarmored_baseline():
    assert armor.compute_ac(None, dex_mod=3) == 13


def test_shield_stacks_with_armor_and_unarmored():
    shield = armor.get_armor("shield")
    leather = armor.get_armor("leather")
    assert shield is not None and leather is not None
    assert armor.compute_ac(leather, dex_mod=4, shield=shield) == 17  # 11+4+2
    assert armor.compute_ac(None, dex_mod=2, shield=shield) == 14  # 10+2+2


def test_chain_mail_strength_13():
    cm = armor.get_armor("chain_mail")
    assert cm is not None
    assert cm.strength_req == 13
    assert armor.compute_ac(cm, dex_mod=3) == 16
    assert armor.imposes_stealth_disadvantage(cm)


def test_unknown_armor_returns_none():
    assert armor.get_armor("mithril_chain") is None


@pytest.mark.parametrize("key,base,dex_mode", [
    ("padded", 11, "add"), ("leather", 11, "add"), ("studded_leather", 12, "add"),
    ("hide", 12, "add_max_2"), ("chain_shirt", 13, "add_max_2"),
    ("scale_mail", 14, "add_max_2"), ("breastplate", 14, "add_max_2"),
    ("half_plate", 15, "add_max_2"),
    ("ring_mail", 14, "none"), ("chain_mail", 16, "none"),
    ("splint", 17, "none"), ("plate", 18, "none"),
    ("shield", 2, "flat"),
])
def test_armor_table_shape(key, base, dex_mode):
    a = armor.get_armor(key)
    assert a is not None
    assert a.base_ac == base
    assert a.dex_mode == dex_mode

