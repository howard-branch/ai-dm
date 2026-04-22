"""
Batch execution with best-effort rollback.

The :class:`BatchExecutor` submits commands one-by-one through the
:class:`SerialCommandQueue` (preserving FIFO + scope serialisation) and
records the live state needed to undo each command. On any failure (or
caller-requested abort) it walks the successful steps in reverse and
issues their inverses.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ai_dm.foundry.command_queue import GLOBAL_SCOPE, SerialCommandQueue
from ai_dm.foundry.registry import FoundryRegistry
from ai_dm.foundry.results import unwrap_single_result
from ai_dm.models.commands import (
    ActivateSceneCommand,
    CreateActorCommand,
    CreateSceneCommand,
    DeleteActorCommand,
    DeleteSceneCommand,
    DeleteTokenCommand,
    GameCommand,
    HighlightObjectCommand,
    MoveTokenCommand,
    ReadActiveSceneCommand,
    ReadActorCommand,
    ReadTokenCommand,
    SpawnTokenCommand,
    UpdateActorCommand,
)

logger = logging.getLogger("ai_dm.foundry.batch")


@dataclass
class StepResult:
    command: GameCommand
    response: dict[str, Any] | None
    ok: bool
    error: str | None = None


@dataclass
class BatchOutcome:
    ok: bool
    results: list[StepResult] = field(default_factory=list)
    rolled_back: list[StepResult] = field(default_factory=list)
    rollback_errors: list[str] = field(default_factory=list)


class CommandInverter:
    """
    Builds inverse commands for the subset of commands whose effect can be
    safely undone. ``snapshot`` is invoked **before** the original command
    runs and may issue read-back commands through the queue.
    """

    def __init__(self, queue: SerialCommandQueue, registry: FoundryRegistry) -> None:
        self.queue = queue
        self.registry = registry

    def snapshot(self, cmd: GameCommand, *, scope_key: str) -> dict[str, Any] | None:
        """Capture pre-state for ``cmd``. Returns an opaque dict or None."""
        if isinstance(cmd, MoveTokenCommand):
            scene_id = cmd.scene_id or self._scene_for_token(cmd.token_id)
            snap = self._read({
                "type": "read_token",
                "scene_id": scene_id,
                "token_id": cmd.token_id,
            }, scope_key=scope_key)
            return {"prev": snap, "scene_id": scene_id}

        if isinstance(cmd, UpdateActorCommand):
            snap = self._read({
                "type": "read_actor",
                "actor_id": cmd.actor_id,
            }, scope_key=scope_key)
            prev_values: dict[str, Any] = {}
            if snap:
                for key in cmd.patch.keys():
                    prev_values[key] = _dotted_get(snap, key)
            return {"prev": prev_values}

        if isinstance(cmd, ActivateSceneCommand):
            snap = self._read({"type": "read_active_scene"}, scope_key=scope_key)
            return {"prev_scene_id": snap.get("id") if snap else None}

        if isinstance(cmd, (SpawnTokenCommand, CreateActorCommand, CreateSceneCommand)):
            return {}  # inverse is delete; no pre-state needed

        return None

    def inverse(
        self,
        original: GameCommand,
        snapshot: dict[str, Any] | None,
        response: dict[str, Any] | None,
    ) -> GameCommand | None:
        if isinstance(original, MoveTokenCommand) and snapshot and snapshot.get("prev"):
            prev = snapshot["prev"]
            return MoveTokenCommand(
                token_id=original.token_id,
                x=int(prev.get("x", original.x)),
                y=int(prev.get("y", original.y)),
                scene_id=snapshot.get("scene_id") or original.scene_id,
            )

        if isinstance(original, UpdateActorCommand) and snapshot is not None:
            prev_values = snapshot.get("prev", {})
            if not prev_values:
                return None
            return UpdateActorCommand(
                actor_id=original.actor_id,
                patch=dict(prev_values),
            )

        if isinstance(original, ActivateSceneCommand) and snapshot is not None:
            prev = snapshot.get("prev_scene_id")
            if not prev or prev == original.scene_id:
                return None
            return ActivateSceneCommand(scene_id=prev)

        if isinstance(original, SpawnTokenCommand):
            token_id = _result_field(response, "tokenId")
            if not token_id:
                return None
            return DeleteTokenCommand(scene_id=original.scene_id, token_id=token_id)

        if isinstance(original, CreateActorCommand):
            actor_id = _result_field(response, "actorId")
            if not actor_id:
                return None
            return DeleteActorCommand(actor_id=actor_id)

        if isinstance(original, CreateSceneCommand):
            scene_id = _result_field(response, "sceneId")
            if not scene_id:
                return None
            return DeleteSceneCommand(scene_id=scene_id)

        # Idempotent / read-only / already-destructive commands have no inverse.
        if isinstance(
            original,
            (
                HighlightObjectCommand,
                DeleteTokenCommand,
                DeleteActorCommand,
                DeleteSceneCommand,
                ReadTokenCommand,
                ReadActorCommand,
                ReadActiveSceneCommand,
            ),
        ):
            return None

        return None

    # ------------------------------------------------------------------ #

    def _read(self, payload: dict[str, Any], *, scope_key: str) -> dict[str, Any] | None:
        try:
            response = self.queue.submit(payload, scope_key=scope_key).result()
            result = unwrap_single_result(response)
            return result.get("snapshot")
        except Exception as exc:  # noqa: BLE001
            logger.warning("snapshot read failed for %s: %s", payload.get("type"), exc)
            return None

    def _scene_for_token(self, token_id: str) -> str | None:
        entry = self.registry.get("token", token_id)
        return entry.scene_id if entry else None


class BatchExecutor:
    def __init__(
        self,
        queue: SerialCommandQueue,
        registry: FoundryRegistry,
        *,
        inverter: CommandInverter | None = None,
    ) -> None:
        self.queue = queue
        self.registry = registry
        self.inverter = inverter or CommandInverter(queue, registry)

    def execute(
        self,
        commands: list[GameCommand],
        *,
        scope_key: str = GLOBAL_SCOPE,
        atomic: bool = True,
        timeout: float | None = None,
    ) -> BatchOutcome:
        outcome = BatchOutcome(ok=True)
        snapshots: list[dict[str, Any] | None] = []
        successes: list[tuple[GameCommand, dict[str, Any] | None, dict[str, Any] | None]] = []

        for cmd in commands:
            snap = self.inverter.snapshot(cmd, scope_key=scope_key)
            snapshots.append(snap)

            try:
                response = self.queue.submit(
                    cmd.model_dump(),
                    scope_key=scope_key,
                    timeout=timeout,
                ).result()
                unwrap_single_result(response)
            except Exception as exc:  # noqa: BLE001
                outcome.ok = False
                outcome.results.append(
                    StepResult(command=cmd, response=None, ok=False, error=str(exc))
                )
                logger.warning("batch step failed: %s — %s", cmd.type, exc)
                if atomic:
                    self._rollback(successes, outcome)
                return outcome

            payload_result = response.get("result") if isinstance(response, dict) else None
            outcome.results.append(
                StepResult(command=cmd, response=payload_result, ok=True)
            )
            successes.append((cmd, snap, payload_result))
            self._update_registry_after_success(cmd, payload_result)

        return outcome

    # ------------------------------------------------------------------ #

    def _rollback(
        self,
        successes: list[tuple[GameCommand, dict[str, Any] | None, dict[str, Any] | None]],
        outcome: BatchOutcome,
    ) -> None:
        for cmd, snap, response in reversed(successes):
            inverse = self.inverter.inverse(cmd, snap, response)
            if inverse is None:
                logger.info("no inverse available for %s; skipping", cmd.type)
                continue
            try:
                self.queue.submit(inverse.model_dump(), scope_key=GLOBAL_SCOPE).result()
                outcome.rolled_back.append(
                    StepResult(command=inverse, response=None, ok=True)
                )
                self._update_registry_after_rollback(inverse)
            except Exception as exc:  # noqa: BLE001
                msg = f"rollback failed for {cmd.type} -> {inverse.type}: {exc}"
                logger.error(msg)
                outcome.rollback_errors.append(msg)

        if outcome.rollback_errors:
            # Surface as data only — caller decides whether to raise.
            logger.critical(
                "rollback completed with %d error(s); state may be inconsistent",
                len(outcome.rollback_errors),
            )

    def _update_registry_after_success(
        self,
        cmd: GameCommand,
        result: dict[str, Any] | None,
    ) -> None:
        if not isinstance(result, dict):
            return
        if isinstance(cmd, CreateSceneCommand) and result.get("sceneId"):
            self.registry.register(
                "scene",
                result["sceneId"],
                name=result.get("sceneName") or cmd.name,
                aliases=(cmd.name,),
            )
        elif isinstance(cmd, CreateActorCommand) and result.get("actorId"):
            self.registry.register(
                "actor",
                result["actorId"],
                name=result.get("actorName") or cmd.name,
                aliases=(cmd.name,),
            )
        elif isinstance(cmd, SpawnTokenCommand) and result.get("tokenId"):
            self.registry.register(
                "token",
                result["tokenId"],
                name=cmd.name or result["tokenId"],
                aliases=(cmd.name,) if cmd.name else (),
                scene_id=cmd.scene_id,
            )

    def _update_registry_after_rollback(self, inverse: GameCommand) -> None:
        if isinstance(inverse, DeleteSceneCommand):
            self.registry.unregister("scene", inverse.scene_id)
        elif isinstance(inverse, DeleteActorCommand):
            self.registry.unregister("actor", inverse.actor_id)
        elif isinstance(inverse, DeleteTokenCommand):
            self.registry.unregister("token", inverse.token_id)


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #

def _result_field(response: dict[str, Any] | None, name: str) -> Any:
    if not isinstance(response, dict):
        return None
    return response.get(name)


def _dotted_get(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur

