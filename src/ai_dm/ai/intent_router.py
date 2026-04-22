"""Wires a parsed :class:`PlayerIntent` into the rest of the system.

For mechanical intents (attack/skill_check) the rules engine resolves
them and we publish the resolution. For ``move``/``interact`` we delegate
to the :class:`CommandRouter` (translating to a Foundry command). Speech
and meta verbs are passed through as data only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ai_dm.ai.intent_schemas import PlayerIntent
from ai_dm.ai.schemas import Command as AICommand
from ai_dm.orchestration.command_router import CommandRouter
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.rules.action_resolver import ActionResolution, ActionResolver
from ai_dm.rules.validators import validate_intent

logger = logging.getLogger("ai_dm.intent.router")


@dataclass
class IntentEnvelope:
    intent: PlayerIntent
    resolution: ActionResolution | None = None
    commands_ok: bool = True
    rejected_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.model_dump(),
            "resolution": self.resolution.to_dict() if self.resolution else None,
            "commands_ok": self.commands_ok,
            "rejected_reason": self.rejected_reason,
        }


class IntentRouter:
    def __init__(
        self,
        *,
        action_resolver: ActionResolver,
        command_router: CommandRouter | None = None,
        event_bus: EventBus | None = None,
        default_scene_id: str | None = None,
    ) -> None:
        self.action_resolver = action_resolver
        self.command_router = command_router
        self.event_bus = event_bus
        self.default_scene_id = default_scene_id

    def handle(self, intent: PlayerIntent, ctx: dict | None = None) -> IntentEnvelope:
        ok, reason = validate_intent(intent)
        if not ok:
            self._publish("intent.rejected", {"intent": intent.model_dump(), "reason": reason})
            return IntentEnvelope(intent=intent, rejected_reason=reason)

        self._publish("intent.parsed", {"intent": intent.model_dump()})

        envelope = IntentEnvelope(intent=intent)
        ctx = ctx or {}

        if intent.type in ("attack", "skill_check"):
            envelope.resolution = self.action_resolver.resolve_intent(intent, ctx)
        elif intent.type == "move" and self.command_router is not None:
            envelope.commands_ok = self._dispatch_move(intent, ctx)
        elif intent.type == "interact" and self.command_router is not None:
            envelope.commands_ok = self._dispatch_highlight(intent)
        # speak / use_item / meta / query_world / unknown: data-only

        self._publish("intent.resolved", envelope.to_dict())
        return envelope

    # ------------------------------------------------------------------ #

    def _dispatch_move(self, intent: PlayerIntent, ctx: dict) -> bool:
        scene_id = ctx.get("scene_id") or self.default_scene_id
        token_id = intent.actor_id  # caller is expected to use a Foundry-known token id
        if not token_id or not intent.target_anchor:
            return False
        cmd = AICommand(
            type="move_token",
            token_id=token_id,
            target=intent.target_anchor,
            scene_id=scene_id,
        )
        try:
            outcome = self.command_router.dispatch([cmd])  # type: ignore[union-attr]
            return outcome.ok
        except Exception as exc:  # noqa: BLE001
            logger.warning("move dispatch failed: %s", exc)
            return False

    def _dispatch_highlight(self, intent: PlayerIntent) -> bool:
        target = intent.target_id or intent.target_anchor
        if not target:
            return False
        try:
            outcome = self.command_router.dispatch([  # type: ignore[union-attr]
                AICommand(type="highlight_object", target_id=target)
            ])
            return outcome.ok
        except Exception as exc:  # noqa: BLE001
            logger.warning("highlight dispatch failed: %s", exc)
            return False

    def _publish(self, name: str, payload: dict) -> None:
        if self.event_bus is None:
            return
        try:
            self.event_bus.publish(name, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("intent event publish failed: %s", exc)

