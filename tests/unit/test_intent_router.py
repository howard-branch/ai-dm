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


# --------------------------------------------------------------------- #
# Authored-interaction → roll.requested server-side fallback
# --------------------------------------------------------------------- #


def _altar_pack(monkeypatch):
    """Build a minimal stand-in for a CampaignPack whose ruined chapel
    has an altar feature with `pray` → Religion DC 12.
    """
    altar_feature = {
        "id": "ruined_altar",
        "name": "Ruined Altar",
        "description": "A shattered slab of basalt.",
        "interactions": [
            {
                "verb": "pray",
                "summary": "Pray to a forgotten god.",
                "check": "int.religion",
                "dc": 12,
                "on_success": "A whisper answers.",
            }
        ],
    }
    node = {"id": "ruined_chapel", "features": [altar_feature]}

    # Stub the three opening_scene helpers IntentRouter consults.
    import ai_dm.app.opening_scene as os_mod
    monkeypatch.setattr(
        os_mod, "find_scene_node",
        lambda pack, sid: node if sid == "ruined_chapel" else None,
    )
    monkeypatch.setattr(os_mod, "find_scene_anchors", lambda pack, sid: [])
    monkeypatch.setattr(os_mod, "find_scene_npcs", lambda pack, sid: [])
    return object()  # any truthy sentinel; the stubs ignore it


def test_interact_pray_emits_roll_requested(monkeypatch):
    pack = _altar_pack(monkeypatch)
    resolver = ActionResolver(rules=RulesEngine())
    cr = MagicMock()
    cr.dispatch.return_value = MagicMock(ok=True)
    bus = EventBus()
    seen: list[dict] = []
    bus.subscribe("roll.requested", lambda p: seen.append(p))
    router = IntentRouter(
        action_resolver=resolver, command_router=cr, event_bus=bus,
        default_scene_id="ruined_chapel", pack=pack,
    )
    intent = PlayerIntent(
        type="interact", verb="pray", actor_id="hero",
        target_anchor="altar", raw_text="I pray at the altar",
    )
    env = router.handle(intent)
    assert env.commands_ok is True  # highlight still dispatched
    assert seen, "expected roll.requested to be published"
    payload = seen[0]
    assert payload["roll_type"] == "skill"
    assert payload["key"] == "religion"
    assert payload["dc"] == 12
    assert payload["actor_id"] == "hero"
    assert payload["scene_id"] == "ruined_chapel"
    assert "religion" in payload["prompt_text"].lower()


def test_interact_alias_verb_still_matches(monkeypatch):
    """Player says "I kneel before the altar" — no exact `kneel` verb
    on the authored interaction, but the alias map maps it to `pray`.
    """
    pack = _altar_pack(monkeypatch)
    resolver = ActionResolver(rules=RulesEngine())
    cr = MagicMock(); cr.dispatch.return_value = MagicMock(ok=True)
    bus = EventBus()
    seen: list[dict] = []
    bus.subscribe("roll.requested", lambda p: seen.append(p))
    router = IntentRouter(
        action_resolver=resolver, command_router=cr, event_bus=bus,
        default_scene_id="ruined_chapel", pack=pack,
    )
    intent = PlayerIntent(
        type="interact", verb="kneel", actor_id="hero",
        target_anchor="ruined altar",
        raw_text="I kneel before the ruined altar",
    )
    router.handle(intent)
    assert seen and seen[0]["key"] == "religion"


def test_interact_without_authored_check_does_not_emit(monkeypatch):
    """A plain feature with no `check`/`dc` must not synthesise a roll."""
    import ai_dm.app.opening_scene as os_mod
    node = {
        "id": "ruined_chapel",
        "features": [{
            "id": "pew", "name": "Wooden Pew",
            "interactions": [{"verb": "sit", "summary": "Rest a moment."}],
        }],
    }
    monkeypatch.setattr(os_mod, "find_scene_node", lambda pack, sid: node)
    monkeypatch.setattr(os_mod, "find_scene_anchors", lambda pack, sid: [])
    monkeypatch.setattr(os_mod, "find_scene_npcs", lambda pack, sid: [])
    resolver = ActionResolver(rules=RulesEngine())
    cr = MagicMock(); cr.dispatch.return_value = MagicMock(ok=True)
    bus = EventBus()
    seen: list[dict] = []
    bus.subscribe("roll.requested", lambda p: seen.append(p))
    router = IntentRouter(
        action_resolver=resolver, command_router=cr, event_bus=bus,
        default_scene_id="ruined_chapel", pack=object(),
    )
    intent = PlayerIntent(
        type="interact", verb="sit", actor_id="hero",
        target_anchor="pew", raw_text="I sit on the pew",
    )
    router.handle(intent)
    assert not seen


def test_parse_check_string_variants():
    parse = IntentRouter._parse_check_string
    assert parse("int.religion") == ("skill", "religion")
    assert parse("wis.perception") == ("skill", "perception")
    assert parse("dex") == ("ability", "dex")
    assert parse("str.save") == ("save", "str")
    assert parse("con_save") == ("save", "con")
    assert parse("athletics") == ("skill", "athletics")
