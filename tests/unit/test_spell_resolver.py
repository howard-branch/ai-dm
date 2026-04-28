"""Unit tests for :class:`ai_dm.rules.spell_resolver.SpellResolver`.

Each test exercises one of the ten contractual responsibilities of the
resolver:

1. eligibility — caster must know / have prepared the spell
2. spell-slot accounting (cantrip free, leveled spell consumes a slot)
3. range / target resolution (delegates to TargetSpec/resolve_targets)
4. attack-roll **or** save branch (driven by the spell record)
5. damage rolling
6. damage application (HP changes, resistances honoured upstream)
7. effects / conditions on a failed save
8. concentration start
9. action-economy consumption (action / bonus / reaction / ritual=free)
10. slot consumption only on a successful cast (refund on bail)
"""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from ai_dm.game.combatant_state import (
    CombatantState,
    Position,
    SpellSlot,
)
from ai_dm.rules.engine import RulesEngine
from ai_dm.rules.spell_resolver import SpellResolver


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #


def _caster(**overrides) -> CombatantState:
    base = dict(
        actor_id="morgana",
        name="Morgana",
        hp=20, max_hp=20, ac=14, team="party",
        spell_slots={
            1: SpellSlot(level=1, current=2, max=2),
            3: SpellSlot(level=3, current=1, max=1),
        },
        cantrips=["chill_touch", "sacred_flame"],
        known_spells=["magic_missile", "hex", "fireball"],
        casting_style="known",
        spell_attack_bonus=5,
        spell_save_dc=13,
    )
    base.update(overrides)
    return CombatantState(**base)


def _foe(**overrides) -> CombatantState:
    base = dict(actor_id="goblin", name="Goblin", hp=10, max_hp=10, ac=12,
                team="foe", saving_throws={"dex": -1, "wis": -1, "con": 0})
    base.update(overrides)
    return CombatantState(**base)


def _intent(**fields) -> SimpleNamespace:
    fields.setdefault("type", "cast_spell")
    fields.setdefault("actor_id", "morgana")
    fields.setdefault("target_id", None)
    fields.setdefault("spell", None)
    fields.setdefault("raw_text", "cast")
    return SimpleNamespace(**fields)


@pytest.fixture
def rules() -> RulesEngine:
    # Deterministic RNG — these tests assert mechanical outcomes.
    return RulesEngine(rng=random.Random(0))


def _resolver(rules, *, caster, foes=None, catalog) -> SpellResolver:
    actors = {caster.actor_id: caster}
    for f in foes or []:
        actors[f.actor_id] = f
    return SpellResolver(
        rules=rules,
        actor_lookup=actors.get,
        spell_catalog=catalog,
    )


# --------------------------------------------------------------------- #
# 1) Eligibility
# --------------------------------------------------------------------- #


def test_eligibility_rejects_unknown_spell(rules):
    caster = _caster()
    res = _resolver(
        rules, caster=caster, catalog={
            "wish": {"name": "Wish", "level": 9, "casting_time": "action"},
        },
    ).cast(
        _intent(spell="wish"),
        ctx={"level": 9},
    )
    assert res.success is False
    assert "not known" in (res.error or "")
    # Slot must NOT have been spent.
    assert caster.spell_slots[3].current == 1


def test_eligibility_blocks_undercast(rules):
    """Cannot cast a level-3 spell at slot level 1."""
    caster = _caster()
    res = _resolver(
        rules, caster=caster, catalog={
            "fireball": {"name": "Fireball", "level": 3, "casting_time": "action"},
        },
    ).cast(_intent(spell="fireball"), ctx={"level": 1})
    assert res.success is False
    assert "level-3" in (res.error or "")


def test_eligibility_skipped_for_unconfigured_caster(rules):
    """Legacy ducks (no cantrips / known / prepared) bypass the gate."""
    caster = CombatantState(
        actor_id="ghost", name="Ghost", hp=10, max_hp=10,
        spell_slots={1: SpellSlot(level=1, current=1, max=1)},
    )
    res = _resolver(
        rules, caster=caster, catalog={
            "bless": {
                "name": "Bless", "level": 1, "casting_time": "action",
                "targeting": {"kind": "self"},
            },
        },
    ).cast(
        _intent(actor_id="ghost", spell="bless"),
        ctx={"level": 1},
    )
    assert res.success


# --------------------------------------------------------------------- #
# 2) Spell-slot accounting
# --------------------------------------------------------------------- #


def test_cantrip_does_not_spend_a_slot(rules):
    caster = _caster()
    res = _resolver(
        rules, caster=caster, catalog={
            "chill_touch": {
                "name": "Chill Touch", "level": 0, "casting_time": "action",
                "range": "120 ft", "targeting": {"kind": "single", "range_ft": 120},
            },
        },
        foes=[_foe()],
    ).cast(
        _intent(spell="chill_touch", target_id="goblin"),
        ctx={"level": 0},
    )
    assert res.success
    assert res.slot_spent is False
    assert caster.spell_slots[1].current == 2  # untouched


def test_leveled_cast_spends_slot(rules):
    caster = _caster()
    res = _resolver(
        rules, caster=caster, catalog={
            "magic_missile": {
                "name": "Magic Missile", "level": 1, "casting_time": "action",
                "targeting": {"kind": "single", "range_ft": 120},
            },
        },
        foes=[_foe()],
    ).cast(
        _intent(spell="magic_missile", target_id="goblin"),
        ctx={"level": 1},
    )
    assert res.success and res.slot_spent
    assert caster.spell_slots[1].current == 1


def test_no_slot_available_fails_soft(rules):
    caster = _caster(spell_slots={1: SpellSlot(level=1, current=0, max=2)})
    res = _resolver(
        rules, caster=caster, catalog={
            "magic_missile": {
                "name": "Magic Missile", "level": 1, "casting_time": "action",
                "targeting": {"kind": "single", "range_ft": 120},
            },
        },
        foes=[_foe()],
    ).cast(
        _intent(spell="magic_missile", target_id="goblin"),
        ctx={"level": 1},
    )
    assert res.success is False
    assert "no level-1 slot" in (res.error or "")


def test_ritual_skips_slot(rules):
    caster = _caster(known_spells=["detect_magic"], rituals=["detect_magic"], ritual_caster=True)
    res = _resolver(
        rules, caster=caster, catalog={
            "detect_magic": {
                "name": "Detect Magic", "level": 1, "casting_time": "10 minutes",
                "targeting": {"kind": "self"},
            },
        },
    ).cast(
        _intent(spell="detect_magic"),
        ctx={"level": 1, "ritual": True},
    )
    assert res.success and res.slot_spent is False
    assert caster.spell_slots[1].current == 2  # untouched


# --------------------------------------------------------------------- #
# 3) Target / range resolution
# --------------------------------------------------------------------- #


def test_target_resolution_self_only_rejects_foreign(rules):
    caster = _caster(known_spells=["shield"])
    res = _resolver(
        rules, caster=caster, catalog={
            "shield": {
                "name": "Shield", "level": 1, "casting_time": "reaction",
                "range": "self", "targeting": {"kind": "self"},
            },
        },
    ).cast(
        _intent(spell="shield", target_id="goblin"),
        ctx={"level": 1},
    )
    assert res.success is False
    # Slot stays full and the reaction is not consumed.
    assert caster.spell_slots[1].current == 2
    assert caster.reaction_used is False


# --------------------------------------------------------------------- #
# 4) Attack-roll branch + 5) damage rolling + 6) damage application
# --------------------------------------------------------------------- #


def test_attack_roll_branch_rolls_damage_and_applies_hp(rules):
    caster = _caster(cantrips=["chill_touch", "sacred_flame", "fire_bolt"])
    foe = _foe(ac=10)  # easy to hit with +5 spell attack bonus
    catalog = {
        "fire_bolt": {
            "name": "Fire Bolt", "level": 0, "casting_time": "action",
            "attack_roll": "ranged",
            "damage": {"parts": [["1d10", "fire"]]},
            "targeting": {"kind": "single", "range_ft": 120},
        },
    }
    res = _resolver(rules, caster=caster, foes=[foe], catalog=catalog).cast(
        _intent(spell="fire_bolt", target_id="goblin"),
        ctx={"level": 0},
    )
    assert res.success
    [outcome] = res.outcomes
    assert outcome.target_id == "goblin"
    # With AC 10 and +5 attack, the deterministic roller should hit.
    assert outcome.hit is True
    assert outcome.damage > 0
    assert outcome.damage_type == "fire"
    assert foe.hp == foe.max_hp - outcome.damage


# --------------------------------------------------------------------- #
# 4) Save branch + half-on-save + 7) effects on failed save
# --------------------------------------------------------------------- #


def test_save_branch_applies_half_damage_on_success(rules):
    """A creature with a huge save bonus should always succeed → half damage."""
    caster = _caster()
    foe = _foe(saving_throws={"dex": 100})
    catalog = {
        "fireball": {
            "name": "Fireball", "level": 3, "casting_time": "action",
            "save": {"ability": "dex", "half_on_save": True},
            "damage": {"parts": [["8d6", "fire"]]},
            "targeting": {"kind": "single", "range_ft": 150},
        },
    }
    res = _resolver(rules, caster=caster, foes=[foe], catalog=catalog).cast(
        _intent(spell="fireball", target_id="goblin"),
        ctx={"level": 3},
    )
    assert res.success
    [outcome] = res.outcomes
    assert outcome.save_success is True
    # Damage was rolled and halved.
    assert outcome.damage > 0
    assert foe.hp == max(0, foe.max_hp - outcome.damage)


def test_save_branch_applies_condition_on_fail(rules):
    """Auto-failing save → effect (condition) is applied."""
    caster = _caster(known_spells=["magic_missile", "hex", "fireball", "cause_fear"])
    foe = _foe(saving_throws={"wis": -100})
    catalog = {
        "cause_fear": {
            "name": "Cause Fear", "level": 1, "casting_time": "action",
            "save": {"ability": "wis"},
            "effects": [{"condition": "frightened", "on": "fail"}],
            "targeting": {"kind": "single", "range_ft": 60},
        },
    }
    res = _resolver(rules, caster=caster, foes=[foe], catalog=catalog).cast(
        _intent(spell="cause_fear", target_id="goblin"),
        ctx={"level": 1},
    )
    assert res.success
    [outcome] = res.outcomes
    assert outcome.save_success is False
    assert "frightened" in outcome.effects_applied
    assert foe.has_condition("frightened")


# --------------------------------------------------------------------- #
# 8) Concentration
# --------------------------------------------------------------------- #


def test_concentration_starts_on_caster(rules):
    caster = _caster()
    foe = _foe()
    catalog = {
        "hex": {
            "name": "Hex", "level": 1, "casting_time": "bonus",
            "duration": "concentration, up to 1 hour",
            "targeting": {"kind": "single", "range_ft": 90},
        },
    }
    res = _resolver(rules, caster=caster, foes=[foe], catalog=catalog).cast(
        _intent(spell="hex", target_id="goblin"),
        ctx={"level": 1},
    )
    assert res.success and res.concentration is True
    assert caster.concentration is not None
    assert caster.concentration.spell_id == "hex"
    assert caster.concentration.target_ids == ["goblin"]


# --------------------------------------------------------------------- #
# 9) Action-economy consumption
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "casting_time,attr",
    [
        ("action", "action_used"),
        ("bonus", "bonus_action_used"),
        ("reaction", "reaction_used"),
    ],
)
def test_action_economy_consumed_per_casting_time(rules, casting_time, attr):
    caster = _caster(known_spells=["test_spell"])
    catalog = {
        "test_spell": {
            "name": "Test", "level": 1, "casting_time": casting_time,
            "targeting": {"kind": "self"},
        },
    }
    res = _resolver(rules, caster=caster, catalog=catalog).cast(
        _intent(spell="test_spell"),
        ctx={"level": 1},
    )
    assert res.success
    assert getattr(caster, attr) is True


def test_already_spent_slot_is_refunded_on_economy_conflict(rules):
    """If the slot was reserved but the action is already spent, refund it."""
    caster = _caster()
    caster.action_used = True  # action already burned this turn
    catalog = {
        "magic_missile": {
            "name": "Magic Missile", "level": 1, "casting_time": "action",
            "targeting": {"kind": "single", "range_ft": 120},
        },
    }
    res = _resolver(rules, caster=caster, foes=[_foe()], catalog=catalog).cast(
        _intent(spell="magic_missile", target_id="goblin"),
        ctx={"level": 1},
    )
    assert res.success is False
    assert "action already spent" in (res.error or "")
    # Slot must be refunded.
    assert caster.spell_slots[1].current == 2


# --------------------------------------------------------------------- #
# Misc — stealth break + structured to_dict for downstream consumers
# --------------------------------------------------------------------- #


def test_successful_cast_breaks_stealth(rules):
    caster = _caster(hidden=True)
    catalog = {
        "magic_missile": {
            "name": "Magic Missile", "level": 1, "casting_time": "action",
            "targeting": {"kind": "single", "range_ft": 120},
        },
    }
    res = _resolver(rules, caster=caster, foes=[_foe()], catalog=catalog).cast(
        _intent(spell="magic_missile", target_id="goblin"),
        ctx={"level": 1},
    )
    assert res.success
    assert caster.hidden is False


def test_result_to_dict_carries_pipeline_summary(rules):
    caster = _caster(cantrips=["chill_touch", "sacred_flame", "fire_bolt"])
    catalog = {
        "fire_bolt": {
            "name": "Fire Bolt", "level": 0, "casting_time": "action",
            "attack_roll": "ranged",
            "damage": {"parts": [["1d10", "fire"]]},
            "targeting": {"kind": "single", "range_ft": 120},
        },
    }
    res = _resolver(rules, caster=caster, foes=[_foe(ac=10)], catalog=catalog).cast(
        _intent(spell="fire_bolt", target_id="goblin"),
        ctx={"level": 0},
    )
    payload = res.to_dict()
    assert payload["spell"] == "fire_bolt"
    assert payload["economy"] == "action"
    assert payload["targets"] == ["goblin"]
    assert payload["targeting"]["kind"] == "single"
    assert payload["outcomes"][0]["target_id"] == "goblin"

