"""Tests for the data-driven SRD condition catalog."""
from __future__ import annotations

from ai_dm.rules.conditions import (
    ALL_CONDITIONS,
    attacker_mod,
    auto_fail_saves,
    crit_on_5ft,
    effects,
    implied,
    merge_advantage,
    speed_zero,
    target_mod,
)


# 15 SRD conditions, including exhaustion.
_EXPECTED = {
    "blinded", "charmed", "deafened", "frightened", "grappled",
    "incapacitated", "invisible", "paralyzed", "petrified", "poisoned",
    "prone", "restrained", "stunned", "unconscious", "exhaustion",
}


def test_catalog_has_fifteen_srd_conditions() -> None:
    assert set(ALL_CONDITIONS) == _EXPECTED
    assert len(ALL_CONDITIONS) == 15


def test_unconscious_implies_prone_and_incapacitated() -> None:
    out = implied({"unconscious"})
    assert {"unconscious", "prone", "incapacitated"} <= out


def test_paralyzed_stunned_petrified_imply_incapacitated() -> None:
    for c in ("paralyzed", "stunned", "petrified"):
        assert "incapacitated" in implied({c})


def test_attacker_mod_disadvantage_when_blinded() -> None:
    m = attacker_mod(["blinded"])
    assert m.disadvantage and not m.advantage


def test_attacker_mod_advantage_when_invisible() -> None:
    m = attacker_mod(["invisible"])
    assert m.advantage and not m.disadvantage


def test_target_mod_advantage_when_prone_or_paralyzed() -> None:
    assert target_mod(["prone"]).advantage
    assert target_mod(["paralyzed"]).advantage


def test_target_mod_disadvantage_when_invisible() -> None:
    assert target_mod(["invisible"]).disadvantage


def test_merge_advantage_cancels() -> None:
    a = attacker_mod(["invisible"])         # advantage
    d = attacker_mod(["blinded"])           # disadvantage
    assert merge_advantage(a, d) == "normal"
    assert merge_advantage(a) == "advantage"
    assert merge_advantage(d) == "disadvantage"


def test_auto_fail_saves_for_paralyzed() -> None:
    assert auto_fail_saves(["paralyzed"]) == {"str", "dex"}
    assert auto_fail_saves(["unconscious"]) == {"str", "dex"}
    assert auto_fail_saves(["blinded"]) == set()


def test_speed_zero_for_grapple_and_unconscious() -> None:
    assert speed_zero(["grappled"]) is True
    assert speed_zero(["unconscious"]) is True
    assert speed_zero(["blinded"]) is False


def test_attacks_within_5ft_crit_for_paralyzed_unconscious() -> None:
    assert crit_on_5ft(["paralyzed"]) is True
    assert crit_on_5ft(["unconscious"]) is True
    assert crit_on_5ft(["blinded"]) is False


def test_effects_returns_copy() -> None:
    e = effects("blinded")
    e["foo"] = "bar"
    assert "foo" not in effects("blinded")  # mutation does not leak

