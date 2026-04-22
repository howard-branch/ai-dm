"""Anchor resolution flowing through CommandRouter."""
from __future__ import annotations

from ai_dm.ai.schemas import Command
from ai_dm.foundry.batch_executor import BatchExecutor, CommandInverter
from ai_dm.foundry.command_queue import SerialCommandQueue
from ai_dm.foundry.registry import FoundryRegistry
from ai_dm.foundry.validator import CommandValidator
from ai_dm.game.location_model import Anchor, SceneLocation
from ai_dm.game.location_service import LocationService
from ai_dm.orchestration.command_router import CommandRouter


class _StubClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def request(self, payload, timeout=10.0, correlation_id=None):
        self.calls.append(payload)
        return {"type": "result", "result": {"ok": True, "command_type": payload.get("type")}}

    def request_batch(self, payloads, timeout=10.0, correlation_id=None):
        return {"type": "result", "result": [{"ok": True} for _ in payloads]}


def test_move_token_resolves_anchor_target():
    registry = FoundryRegistry()
    registry.register("token", "tok-1", name="goblin", scene_id="s1")
    locations = LocationService(registry=registry)
    locations.load_scene(
        SceneLocation(
            scene_id="s1",
            anchors=[Anchor(id="a", name="altar", scene_id="s1", x=120, y=240)],
        )
    )
    client = _StubClient()
    queue = SerialCommandQueue(client, max_pending=10, default_timeout=2.0)  # type: ignore[arg-type]
    executor = BatchExecutor(queue, registry, inverter=CommandInverter(queue, registry))
    router = CommandRouter(
        validator=CommandValidator(registry),
        executor=executor,
        location_service=locations,
    )

    outcome = router.dispatch([
        Command(type="move_token", token_id="goblin", target="altar", scene_id="s1"),
    ])
    queue.shutdown()

    assert outcome.ok is True
    move = next(c for c in client.calls if c["type"] == "move_token")
    assert move["x"] == 120 and move["y"] == 240
    assert move["token_id"] == "tok-1"

