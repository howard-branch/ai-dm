"""Built-in trigger actions.

Each action is a callable ``(payload, ctx) -> None`` constructed from a
declarative spec. Side effects go through the supplied collaborators
(event bus, command router, state store) so triggers are testable.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from ai_dm.ai.schemas import Command as AICommand

logger = logging.getLogger("ai_dm.triggers.actions")

Action = Callable[[dict[str, Any], dict[str, Any]], None]


def publish_event(event_bus, name: str, payload: dict | None = None) -> Action:
    payload = payload or {}

    def _a(ev_payload: dict, ctx: dict) -> None:
        merged = {**payload, "trigger_payload": ev_payload}
        event_bus.publish(name, merged)

    return _a


def set_flag(flags: dict[str, Any], key: str, value: Any) -> Action:
    def _a(_payload: dict, _ctx: dict) -> None:
        flags[key] = value

    return _a


def run_commands(command_router, commands: list[dict]) -> Action:
    def _a(_payload: dict, _ctx: dict) -> None:
        ai_commands = [AICommand.model_validate(c) for c in commands]
        try:
            command_router.dispatch(ai_commands)
        except Exception as exc:  # noqa: BLE001
            logger.warning("trigger command dispatch failed: %s", exc)

    return _a


def speak(narration_bus, text: str, voice: str | None = None) -> Action:
    """Push a narration line through the audio dispatcher (if available)."""

    def _a(_payload: dict, _ctx: dict) -> None:
        try:
            narration_bus.publish(
                "narrator.output_ready",
                {
                    "narration": text,
                    "dialogue": [],
                    "voice": voice,
                    "source": "trigger",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("trigger speak failed: %s", exc)

    return _a


def start_encounter(combat, encounter_id: str, participants: list[dict]) -> Action:
    def _a(_payload: dict, _ctx: dict) -> None:
        try:
            combat.start_encounter(encounter_id, participants)
            combat.roll_initiative()
        except Exception as exc:  # noqa: BLE001
            logger.warning("trigger start_encounter failed: %s", exc)

    return _a


# ---------------------------------------------------------------------- #
# YAML factory
# ---------------------------------------------------------------------- #

def from_spec(spec: dict, *, deps: dict) -> Action:
    """Build an action from a YAML/JSON dict.

    ``deps`` provides the live collaborators (event_bus, command_router,
    flags, combat, etc.) — one Action factory per Container.
    """
    if not isinstance(spec, dict) or len(spec) != 1:
        raise ValueError(f"invalid action spec: {spec!r}")
    (op, args), = spec.items()
    args = args or {}
    if op == "publish_event":
        return publish_event(deps["event_bus"], args["name"], args.get("payload"))
    if op == "set_flag":
        return set_flag(deps["flags"], args["key"], args.get("value", True))
    if op == "run_commands":
        return run_commands(deps["command_router"], args["commands"])
    if op == "speak":
        return speak(deps["event_bus"], args["text"], args.get("voice"))
    if op == "start_encounter":
        return start_encounter(deps["combat"], args["encounter_id"], args.get("participants", []))
    raise ValueError(f"unknown action op: {op!r}")

