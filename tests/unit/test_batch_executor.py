import threading

import pytest

from ai_dm.foundry.batch_executor import BatchExecutor, CommandInverter
from ai_dm.foundry.command_queue import SerialCommandQueue
from ai_dm.foundry.registry import FoundryRegistry
from ai_dm.models.commands import (
    MoveTokenCommand,
    SpawnTokenCommand,
)


class ScriptedClient:
    """Returns canned responses keyed by the command ``type``.

    Each entry in ``responses`` is a list consumed in order. Items can be a
    response dict OR an Exception to raise. ``read_token`` snapshots are
    served from ``token_state``.
    """

    def __init__(self, responses=None, token_state=None) -> None:
        self.responses = {k: list(v) for k, v in (responses or {}).items()}
        self.token_state = token_state or {}
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def request(self, payload, timeout=10.0, correlation_id=None):
        with self._lock:
            self.calls.append(payload)
            ctype = payload.get("type")

            if ctype == "read_token":
                snap = self.token_state.get(payload["token_id"])
                return {"type": "result", "result": {"ok": True, "snapshot": snap}}

            if ctype in self.responses and self.responses[ctype]:
                nxt = self.responses[ctype].pop(0)
                if isinstance(nxt, Exception):
                    raise nxt
                return nxt

            return {"type": "result", "result": {"ok": True, "command_type": ctype}}

    def request_batch(self, payloads, timeout=10.0, correlation_id=None):
        return {"type": "result", "result": [{"ok": True} for _ in payloads]}


def _make(registry=None, **kwargs):
    reg = registry or FoundryRegistry()
    client = ScriptedClient(**kwargs)
    queue = SerialCommandQueue(client, max_pending=64, default_timeout=2.0)
    inverter = CommandInverter(queue, reg)
    executor = BatchExecutor(queue, reg, inverter=inverter)
    return reg, client, queue, executor


def test_batch_happy_path_no_rollback():
    reg = FoundryRegistry()
    reg.register("token", "t1", name="goblin", scene_id="s1")
    reg, client, queue, executor = _make(
        registry=reg, token_state={"t1": {"x": 0, "y": 0}}
    )

    cmds = [MoveTokenCommand(token_id="t1", x=10, y=20, scene_id="s1")]
    outcome = executor.execute(cmds, scope_key="s1")
    queue.shutdown()

    assert outcome.ok is True
    assert outcome.rolled_back == []
    assert any(c["type"] == "move_token" for c in client.calls)


def test_batch_rolls_back_on_failure():
    reg = FoundryRegistry()
    reg.register("token", "t1", name="goblin", scene_id="s1")
    reg.register("scene", "s1", name="Cave")
    reg.register("actor", "a1", name="Goblin")

    responses = {
        "spawn_token": [
            {"type": "result", "result": {"ok": False, "error": "boom"}}
        ],
    }
    reg, client, queue, executor = _make(
        registry=reg,
        responses=responses,
        token_state={"t1": {"x": 5, "y": 5}},
    )

    cmds = [
        MoveTokenCommand(token_id="t1", x=99, y=99, scene_id="s1"),
        SpawnTokenCommand(scene_id="s1", actor_id="a1", x=0, y=0, name="newgob"),
    ]
    outcome = executor.execute(cmds, scope_key="s1")
    queue.shutdown()

    assert outcome.ok is False
    # rolled-back the move_token to its original (5, 5)
    rb_calls = [c for c in client.calls if c["type"] == "move_token"]
    assert any(c.get("x") == 5 and c.get("y") == 5 for c in rb_calls), client.calls
    assert outcome.rolled_back, "expected at least one rollback step"


def test_batch_inverse_for_spawn_is_delete():
    reg = FoundryRegistry()
    reg.register("scene", "s1", name="Cave")
    reg.register("actor", "a1", name="Goblin")

    # spawn succeeds, then a follow-up move_token (unknown token) blows up
    responses = {
        "spawn_token": [
            {"type": "result", "result": {"ok": True, "tokenId": "new-token-1"}}
        ],
        "move_token": [
            {"type": "result", "result": {"ok": False, "error": "missing"}}
        ],
    }
    reg, client, queue, executor = _make(
        registry=reg,
        responses=responses,
        token_state={"t-existing": {"x": 0, "y": 0}},
    )
    reg.register("token", "t-existing", name="existing", scene_id="s1")

    cmds = [
        SpawnTokenCommand(scene_id="s1", actor_id="a1", x=10, y=10, name="goblin2"),
        MoveTokenCommand(token_id="t-existing", x=1, y=1, scene_id="s1"),
    ]
    outcome = executor.execute(cmds, scope_key="s1")
    queue.shutdown()

    assert outcome.ok is False
    delete_calls = [c for c in client.calls if c["type"] == "delete_token"]
    assert delete_calls and delete_calls[0]["token_id"] == "new-token-1"

