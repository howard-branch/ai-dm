from ai_dm.foundry.batch_executor import BatchExecutor, CommandInverter
from ai_dm.foundry.command_queue import SerialCommandQueue
from ai_dm.foundry.registry import FoundryRegistry
from ai_dm.foundry.validator import CommandValidator
from ai_dm.game.state_store import StateStore
from ai_dm.orchestration.command_router import CommandRouter
from ai_dm.orchestration.director import Director


class _StubClient:
    def request(self, payload, timeout=10.0, correlation_id=None):
        return {"type": "result", "result": {"ok": True, "command_type": payload.get("type")}}

    def request_batch(self, payloads, timeout=10.0, correlation_id=None):
        return {"type": "result", "result": [{"ok": True} for _ in payloads]}


def _make_router() -> CommandRouter:
    registry = FoundryRegistry()
    queue = SerialCommandQueue(_StubClient(), max_pending=10, default_timeout=2.0)  # type: ignore[arg-type]
    executor = BatchExecutor(queue, registry, inverter=CommandInverter(queue, registry))
    return CommandRouter(validator=CommandValidator(registry), executor=executor)


def test_director_flow_returns_narration():
    director = Director(state_store=StateStore(), command_router=_make_router())
    result = director.handle_player_input("Look around")
    assert result.narration
