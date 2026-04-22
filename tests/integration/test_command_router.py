from pathlib import Path

from ai_dm.ai.schemas import Command
from ai_dm.foundry.batch_executor import BatchExecutor, CommandInverter
from ai_dm.foundry.client import FoundryClient
from ai_dm.foundry.command_queue import SerialCommandQueue
from ai_dm.foundry.registry import FoundryRegistry
from ai_dm.foundry.validator import CommandValidator
from ai_dm.orchestration.command_router import CommandRouter


class _StubClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def request(self, payload, timeout=10.0, correlation_id=None):
        self.calls.append(payload)
        return {"type": "result", "result": {"ok": True, "command_type": payload.get("type")}}

    def request_batch(self, payloads, timeout=10.0, correlation_id=None):
        return {"type": "result", "result": [{"ok": True} for _ in payloads]}


def test_command_router_dispatches_through_executor():
    client = _StubClient()
    registry = FoundryRegistry()
    registry.register("token", "tok-1", name="sigil_door", scene_id="scene-1")
    registry.register("scene", "scene-1", name="hall")
    queue = SerialCommandQueue(client, max_pending=10, default_timeout=2.0)  # type: ignore[arg-type]
    inverter = CommandInverter(queue, registry)
    executor = BatchExecutor(queue, registry, inverter=inverter)
    router = CommandRouter(
        validator=CommandValidator(registry),
        executor=executor,
    )

    outcome = router.dispatch([
        Command(type="highlight_object", target_id="sigil_door"),
    ])
    queue.shutdown()

    assert outcome.ok is True
    assert any(c["type"] == "highlight_object" for c in client.calls)


def test_command_router_resolves_token_alias():
    client = _StubClient()
    registry = FoundryRegistry()
    registry.register("token", "tok-1", name="goblin", scene_id="scene-1")
    queue = SerialCommandQueue(client, max_pending=10, default_timeout=2.0)  # type: ignore[arg-type]
    executor = BatchExecutor(queue, registry, inverter=CommandInverter(queue, registry))
    router = CommandRouter(validator=CommandValidator(registry), executor=executor)

    outcome = router.dispatch([
        Command(type="move_token", token_id="goblin", x=10, y=20, scene_id="scene-1"),
    ])
    queue.shutdown()

    assert outcome.ok is True
    move_calls = [c for c in client.calls if c["type"] == "move_token"]
    assert move_calls[0]["token_id"] == "tok-1"
