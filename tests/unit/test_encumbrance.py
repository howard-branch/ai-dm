"""Unit tests for encumbrance & carrying capacity rules."""
from __future__ import annotations

from ai_dm.rules import encumbrance as enc


def test_default_carrying_capacity_is_15x_str():
    assert enc.carrying_capacity(10) == 150
    assert enc.carrying_capacity(15) == 225
    assert enc.carrying_capacity(20) == 300


def test_push_drag_lift_is_30x_str():
    assert enc.push_drag_lift(15) == 450


def test_default_status_only_normal_or_heavy():
    assert enc.encumbrance_status(0, 10) == "normal"
    assert enc.encumbrance_status(150, 10) == "normal"
    assert enc.encumbrance_status(151, 10) == "heavy"
    # No 'encumbered' band in the default rule.
    assert enc.encumbrance_status(60, 10) == "normal"


def test_variant_three_bands_at_5x_10x_15x_str():
    # STR 15 → encumbered at >75, heavy at >150
    assert enc.encumbrance_status(70, 15, variant=True) == "normal"
    assert enc.encumbrance_status(76, 15, variant=True) == "encumbered"
    assert enc.encumbrance_status(150, 15, variant=True) == "encumbered"
    assert enc.encumbrance_status(151, 15, variant=True) == "heavy"


def test_speed_penalty_curve():
    assert enc.speed_penalty("normal") == 0
    assert enc.speed_penalty("encumbered") == -10
    assert enc.speed_penalty("heavy") == -20


def test_heavy_disadvantage_categories():
    cats = set(enc.imposes_disadvantage("heavy"))
    assert "attack" in cats
    assert "str_save" in cats and "dex_save" in cats and "con_save" in cats
    assert "str_check" in cats
    assert enc.imposes_disadvantage("normal") == ()
    assert enc.imposes_disadvantage("encumbered") == ()

