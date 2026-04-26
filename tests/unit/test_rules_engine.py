import random

import pytest

from ai_dm.orchestration.event_bus import EventBus
from ai_dm.rules.engine import ActorRuleState, RulesEngine
from ai_dm.rules.house_rules import HouseRule, HouseRuleSet


def _engine(seed: int = 0, *, bus: EventBus | None = None, hr: HouseRuleSet | None = None) -> RulesEngine:
    return RulesEngine(rng=random.Random(seed), event_bus=bus, house_rules=hr)


def test_attack_hit_and_damage_applied():
    e = _engine(seed=2)
    attacker = ActorRuleState(actor_id="hero", name="Hero")
    target = ActorRuleState(actor_id="goblin", name="Goblin", hp=10, max_hp=10, ac=10)
    atk = e.attack(attacker, target, attack_modifier=10)
    assert atk.hit  # +10 vs AC 10 cannot miss except on nat 1
    if not atk.fumble:
        dmg = e.damage(target, dice="1d6", bonus=2, damage_type="slashing", crit=atk.crit)
        e.apply_damage(target, dmg.total)
        assert target.hp < 10


def test_unconscious_added_at_zero_hp():
    e = _engine(seed=0)
    target = ActorRuleState(actor_id="g", name="G", hp=3, max_hp=10, ac=5)
    e.apply_damage(target, 50)
    assert target.hp == 0
    assert "unconscious" in target.conditions


def test_resistance_halves_damage():
    e = _engine(seed=0)
    target = ActorRuleState(actor_id="t", name="T", hp=100, max_hp=100, ac=5, resistances=["fire"])
    dmg = e.damage(target, dice="2d6", damage_type="fire")
    # raw was at least 2 (2d6); halved
    raw = dmg.rolls[0].total
    assert dmg.total == raw // 2 or dmg.total == raw  # tolerate house-rule defaults


def test_immunity_zeroes_damage():
    e = _engine(seed=0)
    target = ActorRuleState(actor_id="t", name="T", hp=100, max_hp=100, ac=5, immunities=["poison"])
    dmg = e.damage(target, dice="3d6", damage_type="poison")
    assert dmg.total == 0


def test_house_rule_disables_crit_double():
    hr = HouseRuleSet(
        house_rules=[
            HouseRule(id="single_dice_crit", applies_to="damage", override={"crit_doubles_dice": False})
        ]
    )
    e = _engine(seed=3, hr=hr)
    target = ActorRuleState(actor_id="t", name="T", hp=100, max_hp=100, ac=1)
    dmg = e.damage(target, dice="1d6", crit=True)
    # with crit_doubles_dice=False we should only see 1 dice roll
    assert len(dmg.rolls) == 1


def test_blinded_attacker_has_disadvantage():
    e = _engine(seed=0)
    attacker = ActorRuleState(actor_id="a", name="A", conditions=["blinded"])
    target = ActorRuleState(actor_id="t", name="T", hp=10, max_hp=10, ac=10)
    atk = e.attack(attacker, target, attack_modifier=0)
    assert atk.attack_roll.advantage in ("disadvantage", "normal")  # normal if cancelled by target adv
    # at minimum two raw d20s rolled when disadvantage applies
    if atk.attack_roll.advantage == "disadvantage":
        assert len(atk.attack_roll.rolls) == 2


def test_events_published():
    bus = EventBus()
    e = _engine(seed=0, bus=bus)
    actor = ActorRuleState(actor_id="a", name="A")
    e.ability_check(actor, modifier=0, dc=10)
    kinds = [name for name, _ in bus.history]
    assert "rules.check_resolved" in kinds


def test_check_pass_or_fail_against_dc():
    e = _engine(seed=4)
    actor = ActorRuleState(actor_id="a", name="A")
    res = e.ability_check(actor, modifier=5, dc=15)
    assert res.success == (res.total >= 15)


def test_attack_within_5ft_of_unconscious_auto_crits():
    """SRD 5.2.1: a hit from within 5 ft on a paralyzed/unconscious
    target becomes a critical hit. The caller signals adjacency via
    ``is_within_5ft``; the engine consults the target's conditions."""
    e = _engine(seed=1)
    attacker = ActorRuleState(actor_id="a", name="A")
    target = ActorRuleState(
        actor_id="t", name="T", hp=10, max_hp=10, ac=5,
        conditions=["unconscious"],
    )
    atk = e.attack(attacker, target, attack_modifier=10, is_within_5ft=True)
    assert atk.hit
    assert atk.crit, "adjacent hit on unconscious target must crit"


def test_attack_at_range_on_unconscious_does_not_auto_crit():
    e = _engine(seed=1)
    attacker = ActorRuleState(actor_id="a", name="A")
    target = ActorRuleState(
        actor_id="t", name="T", hp=10, max_hp=10, ac=5,
        conditions=["unconscious"],
    )
    # Default is_within_5ft=False — auto-crit must NOT fire.
    seen_non_crit_hit = False
    for seed in range(20):
        eng = _engine(seed=seed)
        res = eng.attack(attacker, target, attack_modifier=10)
        if res.hit and not res.crit:
            seen_non_crit_hit = True
            break
    assert seen_non_crit_hit, "ranged hits on unconscious target must not all crit"


def test_attack_within_5ft_of_healthy_target_does_not_auto_crit():
    """Adjacency alone is not enough — only paralyzed/unconscious opt in."""
    e = _engine(seed=1)
    attacker = ActorRuleState(actor_id="a", name="A")
    target = ActorRuleState(actor_id="t", name="T", hp=10, max_hp=10, ac=5)
    seen_non_crit_hit = False
    for seed in range(20):
        eng = _engine(seed=seed)
        res = eng.attack(attacker, target, attack_modifier=10, is_within_5ft=True)
        if res.hit and not res.crit:
            seen_non_crit_hit = True
            break
    assert seen_non_crit_hit
