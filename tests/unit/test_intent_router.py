import random
from unittest.mock import MagicMock

from ai_dm.ai.intent_router import IntentRouter
from ai_dm.ai.intent_schemas import PlayerIntent
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.rules.action_resolver import ActionResolver
from ai_dm.rules.engine import ActorRuleState, RulesEngine


def test_attack_intent_routes_through_rules():
    rules = RulesEngine(rng=random.Random(2))
    target = ActorRuleState(actor_id="goblin", name="g", hp=10, max_hp=10, ac=8)
    resolver = ActionResolver(rules=rules, actor_lookup=lambda aid: target if aid == "goblin" else None)
    bus = EventBus()
    router = IntentRouter(action_resolver=resolver, event_bus=bus)
    intent = PlayerIntent(type="attack", actor_id="hero", target_id="goblin", raw_text="hit goblin")

    env = router.handle(intent, ctx={"attack_modifier": 8})
    assert env.resolution is not None
    assert env.resolution.type == "attack"
    kinds = [n for n, _ in bus.history]
    assert "intent.parsed" in kinds
    assert "intent.resolved" in kinds


def test_invalid_intent_rejected():
    rules = RulesEngine()
    resolver = ActionResolver(rules=rules)
    bus = EventBus()
    router = IntentRouter(action_resolver=resolver, event_bus=bus)
    intent = PlayerIntent(type="attack", target_id=None, raw_text="...")
    env = router.handle(intent)
    assert env.rejected_reason
    kinds = [n for n, _ in bus.history]
    assert "intent.rejected" in kinds


def test_move_intent_dispatches_command():
    rules = RulesEngine()
    resolver = ActionResolver(rules=rules)
    cr = MagicMock()
    cr.dispatch.return_value = MagicMock(ok=True)
    router = IntentRouter(action_resolver=resolver, command_router=cr, default_scene_id="s1")
    intent = PlayerIntent(type="move", actor_id="tok-1", target_anchor="altar", raw_text="move to altar")
    env = router.handle(intent)
    assert env.commands_ok is True
    cr.dispatch.assert_called_once()


def test_speak_intent_passes_through():
    resolver = ActionResolver(rules=RulesEngine())
    router = IntentRouter(action_resolver=resolver)
    intent = PlayerIntent(type="speak", quoted_speech="hi", raw_text='"hi"')
    env = router.handle(intent)
    assert env.resolution is None
    assert env.commands_ok is True

