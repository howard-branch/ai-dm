from __future__ import annotations

import logging
from typing import Iterable

from ai_dm.ai.schemas import Command as AICommand
from ai_dm.foundry.batch_executor import BatchExecutor, BatchOutcome
from ai_dm.foundry.command_queue import GLOBAL_SCOPE
from ai_dm.foundry.errors import RegistryMissError, ValidationError
from ai_dm.foundry.validator import CommandValidator
from ai_dm.game.location_service import LocationService
from ai_dm.models.commands import (
    ActivateSceneCommand,
    GameCommand,
    HighlightObjectCommand,
    MoveActorToCommand,
    MoveTokenCommand,
    UpdateActorCommand,
)

logger = logging.getLogger("ai_dm.orchestration.router")


class CommandRouter:
    """
    Translates AI-emitted commands into validated :class:`GameCommand`
    instances and dispatches them through the BatchExecutor.
    """

    def __init__(
        self,
        *,
        validator: CommandValidator,
        executor: BatchExecutor,
        location_service: LocationService | None = None,
        default_scope: str = GLOBAL_SCOPE,
    ) -> None:
        self.validator = validator
        self.executor = executor
        self.location_service = location_service
        self.default_scope = default_scope
        # Phase 3: hooks called once per command just before validation.
        # Used by the InboundArbiter to fingerprint outbound writes for
        # echo suppression. Each hook receives the raw AICommand.
        self.pre_dispatch_hooks: list = []

    def dispatch(
        self,
        commands: Iterable[AICommand],
        *,
        scope_key: str | None = None,
        atomic: bool = True,
    ) -> BatchOutcome:
        validated: list[GameCommand] = []
        validation_errors: list[str] = []

        for raw in commands:
            for hook in self.pre_dispatch_hooks:
                try:
                    hook(raw)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("pre_dispatch_hook failed: %s", exc)
            payload = self._coerce_to_domain_payload(raw)
            if payload is None:
                continue
            try:
                validated.append(self.validator.validate(payload))
            except ValidationError as exc:
                logger.warning("validation failed: %s", exc)
                validation_errors.append(str(exc))

        scope = scope_key or self.default_scope
        outcome = self.executor.execute(validated, scope_key=scope, atomic=atomic)

        if validation_errors:
            outcome.rollback_errors.extend(
                f"validation:{msg}" for msg in validation_errors
            )

        return outcome

    # ------------------------------------------------------------------ #

    def _coerce_to_domain_payload(self, raw: AICommand) -> dict | None:
        try:
            if raw.type == "move_token":
                x, y = self._resolve_coords(raw)
                return MoveTokenCommand(
                    token_id=self._require(raw.token_id, "token_id"),
                    x=x,
                    y=y,
                    scene_id=raw.scene_id,
                ).model_dump()

            if raw.type == "move_actor_to":
                # Pass actor_id + target through to Foundry — both
                # actor→token and target→coords are resolved server-side.
                # If the LocationService happens to know the anchor,
                # eagerly resolve so older Foundry modules still work.
                x, y = (raw.x, raw.y)
                if (x is None or y is None) and raw.target \
                        and self.location_service is not None:
                    resolved = False
                    if raw.scene_id:
                        try:
                            x, y = self.location_service.resolve_anchor(
                                raw.scene_id, raw.target
                            )
                            resolved = True
                        except RegistryMissError:
                            pass
                    if not resolved:
                        # Fall back to a name lookup across every loaded
                        # scene. The Foundry-supplied scene_id (an opaque
                        # Foundry id) often doesn't match the campaign
                        # pack's scene slug, so the per-scene lookup
                        # misses and the player would otherwise get
                        # "target not found on scene" with no movement.
                        hit = self.location_service.resolve_anywhere(raw.target)
                        if hit is not None:
                            _sid, x, y = hit
                            logger.info(
                                "move_actor_to: anchor %r resolved cross-scene "
                                "from %s → (%s, %s)",
                                raw.target, _sid, x, y,
                            )
                        else:
                            logger.info(
                                "move_actor_to: no anchor matches %r; "
                                "Foundry will try token/note name lookup",
                                raw.target,
                            )
                return MoveActorToCommand(
                    actor_id=self._require(raw.actor_id, "actor_id"),
                    target=raw.target,
                    target_id=raw.target_id,
                    scene_id=raw.scene_id,
                    x=x,
                    y=y,
                    distance_ft=raw.distance_ft,
                    direction=raw.direction,
                    formation_index=raw.formation_index,
                    formation_count=raw.formation_count,
                ).model_dump()

            if raw.type == "activate_scene":
                return ActivateSceneCommand(
                    scene_id=self._require(raw.scene_id, "scene_id"),
                ).model_dump()

            if raw.type == "update_actor":
                return UpdateActorCommand(
                    actor_id=self._require(raw.actor_id, "actor_id"),
                    patch=raw.patch or {},
                ).model_dump()

            if raw.type == "highlight_object":
                return HighlightObjectCommand(
                    target_id=self._require(raw.target_id, "target_id"),
                ).model_dump()

            logger.warning("skipping unsupported command type: %s", raw.type)
            return None
        except ValueError as exc:
            logger.warning("invalid command skipped: %s", exc)
            return None

    def _resolve_coords(self, raw: AICommand) -> tuple[int, int]:
        if raw.x is not None and raw.y is not None:
            return (raw.x, raw.y)
        if raw.target and self.location_service is not None and raw.scene_id:
            try:
                return self.location_service.resolve_anchor(raw.scene_id, raw.target)
            except RegistryMissError as exc:
                raise ValueError(f"unknown anchor {raw.target!r}: {exc}") from exc
        raise ValueError("move_token requires x/y or a resolvable `target`")

    @staticmethod
    def _require(value, field_name: str):
        if value is None:
            raise ValueError(f"Missing required field: {field_name}")
        return value

