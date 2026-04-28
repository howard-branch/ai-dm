"""Unit tests for SRD currency conversion / weight."""
from __future__ import annotations

import pytest

from ai_dm.rules import currency


def test_total_gp_basic():
    assert currency.total_gp({"gp": 5}) == 5
    assert currency.total_gp({"cp": 100}) == 1.0
    assert currency.total_gp({"sp": 10}) == 1.0
    assert currency.total_gp({"ep": 2}) == 1.0
    assert currency.total_gp({"pp": 3}) == 30


def test_coin_weight_50_per_pound():
    assert currency.weight({"gp": 50}) == 1.0
    assert currency.weight({"cp": 25, "sp": 25}) == 1.0
    assert currency.weight({}) == 0.0


def test_subtract_with_change():
    purse = {"gp": 3, "sp": 30}  # 6 gp
    new = currency.subtract(purse, {"gp": 5})
    assert currency.total_gp(new) == 1.0


def test_subtract_insufficient_raises():
    with pytest.raises(currency.InsufficientFunds):
        currency.subtract({"gp": 1}, {"gp": 5})


def test_add_purses():
    a = {"gp": 10}
    b = currency.Coins(sp=20, cp=50)
    out = currency.add(a, b)
    assert out.gp == 10 and out.sp == 20 and out.cp == 50


def test_coins_from_mapping_roundtrip():
    c = currency.Coins.from_mapping({"gp": 7, "sp": 1})
    assert c.as_dict() == {"cp": 0, "sp": 1, "ep": 0, "gp": 7, "pp": 0}

