"""Authority arbitration tests.

Python is authoritative. We verify:

* Self-echoes from outbound writes are suppressed (no policy applied).
* Genuine GM mutations are absorbed into Python state.
* ``revert`` policy pushes an inverse command back through the router.
* Unknown events fall through to the ``default`` policy.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from ai_dm.ai.schemas import Command as AICommand
from ai_dm.foundry.authority import (
    AuthorityPolicy,
    EchoSuppressor,
    InboundArbiter,
)
from ai_dm.foundry.registry import FoundryRegistry
from ai_dm.orchestration.event_bus import EventBus


def _make(policy: AuthorityPolicy | None = None, *, command_router=None):
    bus = EventBus()
    sup = EchoSuppressor(ttl=5.0)
    registry = FoundryRegistry()
    actor_state: dict = {}
    token_state: dict = {}
    scene_state: dict = {}
    arb = InboundArbiter(
        event_bus=bus,
        suppressor=sup,
        registry=registry,
        policy=policy or AuthorityPolicy(),
        actor_state=actor_state,
        token_state=token_state,
        scene_state=scene_state,
        command_router=command_router,
    )
    arb.start()
    return bus, sup, arb, actor_state, token_state, scene_state


def _outcomes(bus: EventBus, event: str) -> list[dict]:
    return [p for n, p in bus.history if n == f"authority.{event}"]


def test_echo_suppressor_round_trip():
    sup = EchoSuppressor(ttl=5.0)
    cmd = AICommand(type="move_token", token_id="tok-1", x=10, y=20, scene_id="s1")
    fp = sup.fingerprint_command(cmd)
    sup.remember(fp)
    assert sup.consume(fp) is True
    # Second consume returns False (already consumed).
    assert sup.consume(fp) is False


def test_self_echo_is_suppressed_and_not_absorbed():
    bus, _sup, arb, _astate, token_state, _sstate = _make()
    cmd = AICommand(type="move_token", token_id="tok-1", x=10, y=20, scene_id="s1")
    arb.prime_outbound(cmd)

    bus.publish("foundry.token_moved", {
        "token_id": "tok-1", "x": 10, "y": 20, "scene_id": "s1",
    })

    outs = _outcomes(bus, "token_moved")
    assert outs and outs[-1]["self_echo"] is True
    assert outs[-1]["absorbed"] is False
    # Suppressed echoes do NOT touch local state.
    assert "tok-1" not in token_state


def test_external_token_move_absorbed():
    bus, _sup, _arb, _astate, token_state, _sstate = _make()

    bus.publish("foundry.token_moved", {
        "token_id": "tok-9", "x": 200, "y": 300, "scene_id": "s1",
    })

    outs = _outcomes(bus, "token_moved")
    assert outs[-1]["absorbed"] is True
    assert outs[-1]["self_echo"] is False
    assert token_state["tok-9"] == {"x": 200, "y": 300, "scene_id": "s1"}


def test_external_actor_hp_absorbed():
    bus, _sup, _arb, actor_state, _ts, _ss = _make()
    bus.publish("foundry.actor_updated", {
        "actor_id": "a-1", "change": {}, "hp": 4,
    })
    assert actor_state["a-1"]["hp"] == 4


def test_scene_activated_absorbed():
    bus, _sup, _arb, _as, _ts, scene_state = _make()
    bus.publish("foundry.scene_activated", {
        "scene_id": "s-2", "scene_name": "Crypt",
    })
    assert scene_state["current_scene_id"] == "s-2"
    assert scene_state["current_scene_name"] == "Crypt"


def test_revert_policy_pushes_inverse_token_move():
    cr = MagicMock()
    cr.dispatch.return_value = MagicMock(ok=True)
    policy = AuthorityPolicy(token_moved="revert")
    bus, _sup, _arb, _as, token_state, _ss = _make(policy, command_router=cr)
    # Seed previous-known position so we can revert to it.
    token_state["tok-1"] = {"x": 10, "y": 20, "scene_id": "s1"}

    bus.publish("foundry.token_moved", {
        "token_id": "tok-1", "x": 999, "y": 999, "scene_id": "s1",
    })

    cr.dispatch.assert_called_once()
    sent = cr.dispatch.call_args[0][0]
    assert sent[0].type == "move_token"
    assert sent[0].x == 10 and sent[0].y == 20
    out = _outcomes(bus, "token_moved")[-1]
    assert out["reverted"] is True


def test_revert_skipped_when_no_known_previous_state():
    cr = MagicMock()
    cr.dispatch.return_value = MagicMock(ok=True)
    policy = AuthorityPolicy(token_moved="revert")
    bus, _sup, _arb, _as, _ts, _ss = _make(policy, command_router=cr)
    bus.publish("foundry.token_moved", {
        "token_id": "novel", "x": 1, "y": 1, "scene_id": "s1",
    })
    # No prior state for "novel" → cannot revert; we emit reverted=False.
    cr.dispatch.assert_not_called()
    out = _outcomes(bus, "token_moved")[-1]
    assert out["reverted"] is False
    assert out["reason"] == "no_inverse"


def test_revert_corrective_write_is_self_suppressed():
    """The inverse we send must not itself trigger another revert."""
    sent_commands: list = []

    class _CR:
        def dispatch(self, commands):
            sent_commands.extend(commands)
            # Simulate Foundry echoing the corrective write back to us.
            for cmd in commands:
                if cmd.type == "move_token":
                    bus.publish("foundry.token_moved", {
                        "token_id": cmd.token_id,
                        "x": cmd.x,
                        "y": cmd.y,
                        "scene_id": cmd.scene_id,
                    })
            return MagicMock(ok=True)

    policy = AuthorityPolicy(token_moved="revert")
    bus, _sup, _arb, _as, token_state, _ss = _make(policy, command_router=_CR())
    token_state["tok-1"] = {"x": 10, "y": 20, "scene_id": "s1"}

    bus.publish("foundry.token_moved", {
        "token_id": "tok-1", "x": 999, "y": 999, "scene_id": "s1",
    })

    # Exactly ONE corrective write — the echo of our own correction was
    # suppressed and did not loop.
    moves = [c for c in sent_commands if c.type == "move_token"]
    assert len(moves) == 1
    self_echo_count = sum(
        1 for o in _outcomes(bus, "token_moved") if o["self_echo"]
    )
    assert self_echo_count == 1


def test_default_policy_for_unknown_event():
    bus, _sup, _arb, _as, _ts, _ss = _make()
    # No subscriber for arbitrary event → arbiter ignores it gracefully.
    bus.publish("foundry.something_unhandled", {"x": 1})
    # arbiter only listens to known events; nothing should crash.
    assert _outcomes(bus, "something_unhandled") == []


def test_router_pre_dispatch_hook_primes_suppressor():
    """End-to-end: dispatching through CommandRouter primes the suppressor."""
    from ai_dm.foundry.batch_executor import BatchExecutor, CommandInverter
    from ai_dm.foundry.command_queue import SerialCommandQueue
    from ai_dm.foundry.validator import CommandValidator
    from ai_dm.orchestration.command_router import CommandRouter

    class _StubClient:
        def request(self, payload, timeout=10.0, correlation_id=None):
            return {"type": "result", "result": {"ok": True, "command_type": payload.get("type")}}

        def request_batch(self, payloads, timeout=10.0, correlation_id=None):
            return {"type": "result", "result": [{"ok": True} for _ in payloads]}

    registry = FoundryRegistry()
    registry.register("token", "tok-1", name="goblin", scene_id="scene-1")
    queue = SerialCommandQueue(_StubClient(), max_pending=10, default_timeout=2.0)  # type: ignore[arg-type]
    executor = BatchExecutor(queue, registry, inverter=CommandInverter(queue, registry))
    router = CommandRouter(validator=CommandValidator(registry), executor=executor)

    bus = EventBus()
    sup = EchoSuppressor(ttl=5.0)
    arb = InboundArbiter(
        event_bus=bus,
        suppressor=sup,
        registry=registry,
        actor_state={}, token_state={}, scene_state={},
        command_router=router,
    )
    arb.start()
    router.pre_dispatch_hooks.append(arb.prime_outbound)

    router.dispatch([
        AICommand(type="move_token", token_id="tok-1", x=15, y=25, scene_id="scene-1"),
    ])
    queue.shutdown()

    # Now the matching echo is suppressed.
    bus.publish("foundry.token_moved", {
        "token_id": "tok-1", "x": 15, "y": 25, "scene_id": "scene-1",
    })
    out = _outcomes(bus, "token_moved")[-1]
    assert out["self_echo"] is True

