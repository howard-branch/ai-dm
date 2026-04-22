import random
from types import SimpleNamespace

from ai_dm.rules.action_resolver import ActionResolver
from ai_dm.rules.engine import ActorRuleState, RulesEngine


def test_legacy_string_overload_returns_freeform():
    r = ActionResolver()
    out = r.resolve("open the door")
    assert out == {"type": "freeform", "text": "open the door"}


def test_attack_intent_resolved_against_rules():
    rules = RulesEngine(rng=random.Random(2))
    target = ActorRuleState(actor_id="goblin", name="Goblin", hp=10, max_hp=10, ac=8)

    def lookup(aid: str):
        return target if aid == "goblin" else None

    resolver = ActionResolver(rules=rules, actor_lookup=lookup)
    intent = SimpleNamespace(type="attack", actor_id="hero", target_id="goblin", raw_text="attack")
    res = resolver.resolve_intent(intent, ctx={"attack_modifier": 10, "damage_dice": "1d4"})
    assert res.type == "attack"
    if res.success:
        assert res.details["target_hp"] < 10


def test_skill_check_intent():
    rules = RulesEngine(rng=random.Random(0))
    resolver = ActionResolver(rules=rules)
    intent = SimpleNamespace(type="skill_check", actor_id="p", skill="stealth", dc=10, modifier=5)
    res = resolver.resolve_intent(intent, ctx={})
    assert res.type == "skill_check"
    assert "DC 10" in res.summary


def test_unknown_kind_returns_freeform():
    resolver = ActionResolver()
    res = resolver.resolve_intent(SimpleNamespace(type="foo"), ctx={})
    assert res.type == "freeform"
