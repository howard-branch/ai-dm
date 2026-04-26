"""Validation helpers for player actions / intents."""
from __future__ import annotations

from typing import Any

_KNOWN_INTENT_TYPES = {
    "move",
    "travel",
    "attack",
    "cast_spell",
    "skill_check",
    "interact",
    "speak",
    "use_item",
    # Combat action menu.
    "dash",
    "disengage",
    "dodge",
    "help",
    "hide",
    "ready",
    "end_turn",
    "query_world",
    "meta",
}


def validate_player_action(action: dict) -> bool:
    """Legacy contract — true if ``action`` has a 'type' key."""
    return "type" in action


def validate_intent(intent: Any) -> tuple[bool, str | None]:
    """Validate a structured intent object/dict.

    Returns ``(ok, reason_if_failed)``.
    """
    kind = (
        getattr(intent, "type", None)
        or (intent.get("type") if isinstance(intent, dict) else None)
    )
    if kind is None:
        return False, "missing type"
    if kind not in _KNOWN_INTENT_TYPES:
        return False, f"unknown intent type: {kind!r}"
    if kind == "attack":
        target = (
            getattr(intent, "target_id", None)
            or (intent.get("target_id") if isinstance(intent, dict) else None)
        )
        if not target:
            return False, "attack intent requires target_id"
    if kind == "skill_check":
        skill = (
            getattr(intent, "skill", None)
            or (intent.get("skill") if isinstance(intent, dict) else None)
        )
        if not skill:
            return False, "skill_check intent requires skill"
    return True, None

