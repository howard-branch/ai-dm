"""Player chat-attack → Foundry roll dialog → resumed resolution."""
from __future__ import annotations

import random
from typing import Any

import pytest

from ai_dm.ai.intent_router import IntentRouter
from ai_dm.ai.intent_schemas import PlayerIntent
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.orchestration.pc_attack_resolver import PCAttackResolver
from ai_dm.rules.action_resolver import ActionResolver
from ai_dm.rules.attack import make_attack
from ai_dm.rules.dice import DiceRoller
from ai_dm.rules.engine import ActorRuleState, RulesEngine


# --------------------------------------------------------------------- #
# 1. make_attack honours preroll_d20 (no dice spin, nat-20/1 still apply)
# --------------------------------------------------------------------- #


def test_make_attack_preroll_skips_dice_and_uses_value() -> None:
    roller = DiceRoller(seed=0)
    res = make_attack(
        roller, attack_modifier=4, target_ac=15, preroll_d20=12,
    )
    assert res.attack_roll.kept == [12]
    assert res.attack_roll.total == 12
    assert res.total == 16
    assert res.hit is True
    assert res.crit is False
    assert res.fumble is False


def test_make_attack_preroll_nat_20_is_crit() -> None:
    res = make_attack(
        DiceRoller(seed=99), attack_modifier=0, target_ac=99, preroll_d20=20,
    )
    assert res.crit is True
    assert res.hit is True


def test_make_attack_preroll_nat_1_is_fumble() -> None:
    res = make_attack(
        DiceRoller(seed=99), attack_modifier=99, target_ac=2, preroll_d20=1,
    )
    assert res.fumble is True
    assert res.hit is False


# --------------------------------------------------------------------- #
# 2. RulesEngine.attack threads the preroll through
# --------------------------------------------------------------------- #


def test_rules_engine_attack_uses_preroll() -> None:
    rules = RulesEngine(rng=random.Random(0))
    a = ActorRuleState(actor_id="a", name="A")
    t = ActorRuleState(actor_id="t", name="T", ac=15)
    res = rules.attack(a, t, attack_modifier=3, preroll_d20=12)
    assert res.attack_roll.kept == [12]
    assert res.total == 15
    assert res.hit is True


# --------------------------------------------------------------------- #
# 3. ActionResolver._resolve_attack reads ctx["preroll_d20"]
# --------------------------------------------------------------------- #


def test_action_resolver_uses_preroll_from_ctx() -> None:
    rules = RulesEngine(rng=random.Random(0))
    resolver = ActionResolver(rules=rules)
    intent = PlayerIntent(
        type="attack", actor_id="player", target_id="orc", raw_text="attack orc",
    )
    out = resolver.resolve_intent(intent, ctx={"preroll_d20": 19})
    assert out.type == "attack"
    # 19 + 0 vs default AC 10 → hit.
    assert out.success is True
    assert out.details["attack"]["attack_roll"]["kept"] == [19]


# --------------------------------------------------------------------- #
# 4. IntentRouter defers a player chat attack to roll.requested
# --------------------------------------------------------------------- #


class _PCStub:
    """Duck-typed combatant: looks player-controlled to IntentRouter.

    Carries enough of the :class:`CombatantState` surface for
    :class:`RulesEngine.attack` to resolve against it (conditions,
    resistances, hp, action-economy flags) so the synchronous
    fallback paths in our tests don't crash on missing attrs.
    """

    def __init__(self, *, actor_id: str = "jon", controller: str = "player") -> None:
        self.actor_id = actor_id
        self.name = actor_id.title()
        self.controller = controller
        self.ac = 14
        self.hp = 20
        self.max_hp = 20
        self.ability_mods = {"str": 3, "dex": 1}
        self.proficiency_bonus = 2
        self.conditions = []
        self.resistances: list[str] = []
        self.vulnerabilities: list[str] = []
        self.immunities: list[str] = []
        self.exhaustion = 0
        self.action_used = False
        self.bonus_action_used = False
        self.reaction_used = False
        self.hidden = False
        self.team = "party" if controller == "player" else "foe"


class _FoeStub(_PCStub):
    def __init__(self) -> None:
        super().__init__(actor_id="mon.grukk", controller="ai")
        self.ac = 13
        self.hp = 15
        self.max_hp = 15
        self.team = "foe"


def _router_with_lookup(bus: EventBus) -> tuple[IntentRouter, ActionResolver]:
    pc = _PCStub()
    foe = _FoeStub()
    table = {pc.actor_id: pc, foe.actor_id: foe}
    resolver = ActionResolver(
        rules=RulesEngine(rng=random.Random(1)),
        actor_lookup=lambda key: table.get(key),
    )
    router = IntentRouter(
        action_resolver=resolver,
        event_bus=bus,
    )
    return router, resolver


def test_intent_router_defers_pc_attack_to_roll_requested() -> None:
    bus = EventBus()
    captured: list[dict] = []
    bus.subscribe("roll.requested", lambda p: captured.append(p))

    router, _ = _router_with_lookup(bus)
    intent = PlayerIntent(
        type="attack", actor_id="jon", target_id="mon.grukk",
        raw_text="attack grukk",
    )
    envelope = router.handle(intent, ctx={"scene_id": "stone_chamber"})

    # Deferred: no synchronous resolution attached.
    assert envelope.resolution is None
    # Exactly one roll dialog was requested.
    assert len(captured) == 1
    payload = captured[0]
    assert payload["roll_type"] == "attack"
    assert payload["actor_id"] == "jon"
    assert payload["ac"] == 13
    # +5 = STR mod 3 + prof 2.
    assert payload["formula"] == "1d20+5"
    corr = payload["correlation"]
    assert corr["kind"] == "pc_attack"
    assert corr["actor_id"] == "jon"
    assert corr["target_id"] == "mon.grukk"


def test_intent_router_does_not_defer_when_preroll_already_supplied() -> None:
    bus = EventBus()
    requested: list[dict] = []
    bus.subscribe("roll.requested", lambda p: requested.append(p))

    router, _ = _router_with_lookup(bus)
    intent = PlayerIntent(
        type="attack", actor_id="jon", target_id="mon.grukk",
        raw_text="attack grukk",
    )
    envelope = router.handle(
        intent, ctx={"scene_id": "stone_chamber", "preroll_d20": 18},
    )
    # Resolved synchronously with the supplied d20 → no dialog.
    assert envelope.resolution is not None
    assert envelope.resolution.type == "attack"
    assert requested == []


def test_intent_router_does_not_defer_macro_origin() -> None:
    bus = EventBus()
    requested: list[dict] = []
    bus.subscribe("roll.requested", lambda p: requested.append(p))

    router, _ = _router_with_lookup(bus)
    intent = PlayerIntent(
        type="attack", actor_id="jon", target_id="mon.grukk",
        raw_text="attack grukk",
    )
    envelope = router.handle(
        intent,
        ctx={
            "scene_id": "stone_chamber", "origin": "macro",
            "attack_modifier": 5, "damage_dice": "1d8", "damage_bonus": 3,
        },
    )
    assert envelope.resolution is not None
    assert requested == []


def test_intent_router_does_not_defer_npc_controller() -> None:
    bus = EventBus()
    requested: list[dict] = []
    bus.subscribe("roll.requested", lambda p: requested.append(p))

    pc = _PCStub()
    foe = _FoeStub()
    table = {pc.actor_id: pc, foe.actor_id: foe}
    resolver = ActionResolver(
        rules=RulesEngine(rng=random.Random(1)),
        actor_lookup=lambda key: table.get(key),
    )
    router = IntentRouter(action_resolver=resolver, event_bus=bus)

    # Foe attacking PC: not deferred.
    intent = PlayerIntent(
        type="attack", actor_id="mon.grukk", target_id="jon",
        raw_text="grukk attacks jon",
    )
    envelope = router.handle(intent, ctx={"scene_id": "stone_chamber"})
    assert envelope.resolution is not None
    assert requested == []


# --------------------------------------------------------------------- #
# 5. PCAttackResolver picks up roll.resolved and finishes the attack
# --------------------------------------------------------------------- #


def test_pc_attack_resolver_resumes_with_player_d20() -> None:
    bus = EventBus()

    class _RecordingResolver:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, dict]] = []

        def resolve_intent(self, intent: Any, ctx: dict) -> Any:
            self.calls.append((intent, ctx))

            class _R:
                summary = "ok"
            return _R()

    rec = _RecordingResolver()
    resolver_sub = PCAttackResolver(event_bus=bus, action_resolver=rec)
    resolver_sub.start(synchronous=True)

    bus.publish("roll.resolved", {
        "record": {
            "kept": [17], "rolls": [17], "total": 22, "modifier": 5,
            "actor_id": "jon",
        },
        "source": "player",
        "correlation": {
            "kind": "pc_attack",
            "actor_id": "jon",
            "target_id": "mon.grukk",
            "weapon": "greatsword",
            "scene_id": "stone_chamber",
        },
    })

    assert len(rec.calls) == 1
    intent, ctx = rec.calls[0]
    assert intent.type == "attack"
    assert intent.actor_id == "jon"
    assert intent.target_id == "mon.grukk"
    assert intent.weapon == "greatsword"
    assert ctx["preroll_d20"] == 17
    assert ctx["scene_id"] == "stone_chamber"
    assert ctx["origin"] == "pc_attack_resume"


def test_pc_attack_resolver_ignores_non_pc_attack_correlations() -> None:
    bus = EventBus()
    calls: list[Any] = []

    class _Resolver:
        def resolve_intent(self, intent, ctx):  # noqa: D401
            calls.append((intent, ctx))

    PCAttackResolver(event_bus=bus, action_resolver=_Resolver()).start(synchronous=True)
    bus.publish("roll.resolved", {
        "record": {"kept": [10], "total": 10},
        "correlation": {"kind": "skill_check"},
    })
    assert calls == []


# --------------------------------------------------------------------- #
# 6. PCAttackResolver advances combat after a player attack
# --------------------------------------------------------------------- #


class _StubCombatState:
    def __init__(self, current_actor_id: str) -> None:
        class _P:
            def __init__(self, aid):
                self.actor_id = aid
        self.participants = [_P(current_actor_id), _P("mon.grukk")]
        self.current_index = 0
        self.phase = "in_round"


class _StubCombat:
    def __init__(self, current_actor_id: str = "jon") -> None:
        self.state = _StubCombatState(current_actor_id)


class _StubTurnManager:
    def __init__(self) -> None:
        self.next_turn_calls = 0

    def next_turn(self) -> None:
        self.next_turn_calls += 1


def test_pc_attack_resolver_advances_turn_when_combat_live() -> None:
    bus = EventBus()
    tm = _StubTurnManager()
    combat = _StubCombat(current_actor_id="jon")

    class _Resolver:
        def resolve_intent(self, intent, ctx):
            class _R:
                summary = "ok"
            return _R()

    PCAttackResolver(
        event_bus=bus, action_resolver=_Resolver(),
        turn_manager=tm, combat=combat,
    ).start(synchronous=True)

    bus.publish("roll.resolved", {
        "record": {"kept": [12], "total": 17, "modifier": 5},
        "correlation": {
            "kind": "pc_attack",
            "actor_id": "jon", "target_id": "mon.grukk",
        },
    })
    assert tm.next_turn_calls == 1


def test_pc_attack_resolver_does_not_advance_when_combat_idle() -> None:
    bus = EventBus()
    tm = _StubTurnManager()
    combat = _StubCombat()
    combat.state = None  # no encounter live

    class _Resolver:
        def resolve_intent(self, intent, ctx):
            return type("R", (), {"summary": "ok"})()

    PCAttackResolver(
        event_bus=bus, action_resolver=_Resolver(),
        turn_manager=tm, combat=combat,
    ).start(synchronous=True)
    bus.publish("roll.resolved", {
        "record": {"kept": [12]},
        "correlation": {
            "kind": "pc_attack",
            "actor_id": "jon", "target_id": "mon.grukk",
        },
    })
    assert tm.next_turn_calls == 0


def test_pc_attack_resolver_does_not_advance_when_attacker_off_turn() -> None:
    """E.g. an opportunity attack: the attacker isn't the current
    initiative slot, so we must NOT touch the turn pointer."""
    bus = EventBus()
    tm = _StubTurnManager()
    combat = _StubCombat(current_actor_id="someone_else")

    class _Resolver:
        def resolve_intent(self, intent, ctx):
            return type("R", (), {"summary": "ok"})()

    PCAttackResolver(
        event_bus=bus, action_resolver=_Resolver(),
        turn_manager=tm, combat=combat,
    ).start(synchronous=True)
    bus.publish("roll.resolved", {
        "record": {"kept": [12]},
        "correlation": {
            "kind": "pc_attack",
            "actor_id": "jon", "target_id": "mon.grukk",
        },
    })
    assert tm.next_turn_calls == 0


def test_pc_attack_resolver_advances_via_actor_lookup_alias() -> None:
    """Foundry id (``"yZyrz..."``) differs from the live combatant id
    (``"hero"``); resolving through ``actor_lookup`` lets us see they
    refer to the same actor and advance the turn correctly."""
    bus = EventBus()
    tm = _StubTurnManager()
    combat = _StubCombat(current_actor_id="hero")

    class _Combatant:
        actor_id = "hero"

    class _Resolver:
        # Only resolve_intent + actor_lookup are needed by the subscriber.
        actor_lookup = staticmethod(lambda key: _Combatant() if key == "yZyrz" else None)

        def resolve_intent(self, intent, ctx):
            return type("R", (), {"summary": "ok"})()

    PCAttackResolver(
        event_bus=bus, action_resolver=_Resolver(),
        turn_manager=tm, combat=combat,
    ).start(synchronous=True)
    bus.publish("roll.resolved", {
        "record": {"kept": [12]},
        "correlation": {
            "kind": "pc_attack",
            "actor_id": "yZyrz",        # raw Foundry id
            "target_id": "mon.grukk",
        },
    })
    assert tm.next_turn_calls == 1


# --------------------------------------------------------------------- #
# 7. IntentEnvelope.deferred is set on the deferred branch
# --------------------------------------------------------------------- #


def test_deferred_envelope_flag() -> None:
    bus = EventBus()
    router, _ = _router_with_lookup(bus)
    intent = PlayerIntent(
        type="attack", actor_id="jon", target_id="mon.grukk",
        raw_text="attack grukk",
    )
    env = router.handle(intent, ctx={"scene_id": "stone_chamber"})
    assert env.deferred is True
    assert env.resolution is None


def test_non_deferred_envelope_flag_is_false() -> None:
    bus = EventBus()
    router, _ = _router_with_lookup(bus)
    intent = PlayerIntent(
        type="attack", actor_id="jon", target_id="mon.grukk",
        raw_text="attack grukk",
    )
    env = router.handle(
        intent, ctx={"scene_id": "stone_chamber", "preroll_d20": 18},
    )
    assert env.deferred is False
    assert env.resolution is not None


# --------------------------------------------------------------------- #
# 8. Director skips intent dispatch on resume follow-ups
# --------------------------------------------------------------------- #


def test_director_skips_intent_dispatch_on_resume_origin() -> None:
    """The follow-up player_input synthesised by
    RollRequestDispatcher._enqueue_followup carries the original
    utterance verbatim ("attack grukk") inside the [roll-result …]
    text. Without the origin guard the parser re-detects an
    ``attack`` intent and the deferred-attack code path opens
    *another* dialog every turn — infinitely.
    """
    from ai_dm.orchestration.director import Director
    from ai_dm.ai.intent_parser import IntentParser
    from ai_dm.ai.narrator import Narrator

    class _State:
        def get_context(self): return {}
        def apply_state_updates(self, _): pass

    class _CmdRouter:
        def dispatch(self, _cmds):
            return type("O", (), {"ok": True, "results": [], "rollback_errors": []})()

    class _Narrator(Narrator):
        def __init__(self): pass
        def narrate(self, *, player_input, context):
            from ai_dm.ai.schemas import AIOutput
            return AIOutput(narration="narrated")

    class _CountingRouter:
        def __init__(self): self.calls = 0

        def handle(self, intent, ctx):
            self.calls += 1
            from ai_dm.ai.intent_router import IntentEnvelope
            return IntentEnvelope(intent=intent)

    counting = _CountingRouter()
    director = Director(
        _State(), _CmdRouter(),
        narrator=_Narrator(),
        intent_parser=IntentParser(),
        intent_router=counting,
    )

    # Origin == resume → router.handle MUST NOT be called.
    text = (
        '[roll-result player] attack = 19 vs AC 13 → success\n'
        '  said: "attack grukk"\n'
        '  Narrate the consequence above for the player.'
    )
    director.handle_player_input(
        text, scene_id="stone_chamber", actor_id="jon",
        origin="roll_request_dispatcher",
    )
    assert counting.calls == 0

    # Origin absent → router.handle IS called (parser detects attack).
    director.handle_player_input(
        "attack grukk", scene_id="stone_chamber", actor_id="jon",
    )
    assert counting.calls == 1


