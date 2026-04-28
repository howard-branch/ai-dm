"""Action economy — SRD 5.2.

Single source of truth: ``assets/srd5_2/core/actions.json``.

Consolidates the action-economy bookkeeping previously duplicated
across ``ActionResolver._consume_economy``.
"""
from __future__ import annotations

from typing import Any, Literal

from ai_dm.rules.srd_core import load

_DATA = load("actions")

EconomyKey = Literal["action", "bonus_action", "reaction", "free"]
ECONOMY_KEYS: tuple[EconomyKey, ...] = tuple(_DATA["economy_keys"])  # type: ignore[assignment]

_ACTIONS = list(_DATA["standard_actions"])
ACTION_KEYS: tuple[str, ...] = tuple(a["key"] for a in _ACTIONS)
_ECONOMY_BY_KEY: dict[str, EconomyKey] = {
    a["key"]: a["economy"] for a in _ACTIONS  # type: ignore[misc]
}


def economy_for(action_key: str) -> EconomyKey:
    """Return the action-economy slot for ``action_key`` (default ``"action"``)."""
    return _ECONOMY_BY_KEY.get(action_key, "action")


def is_used(actor: Any, slot: EconomyKey) -> bool:
    if slot == "free":
        return False
    attr = {
        "action": "action_used",
        "bonus_action": "bonus_action_used",
        "reaction": "reaction_used",
    }[slot]
    return bool(getattr(actor, attr, False))


def consume(actor: Any, slot_or_action: str) -> bool:
    """Mark the requested economy slot as spent; ``False`` if already spent.

    ``slot_or_action`` may be an :data:`EconomyKey` or one of
    :data:`ACTION_KEYS` (which is mapped to its declared slot).
    """
    if actor is None:
        return True
    if slot_or_action in _ECONOMY_BY_KEY:
        slot: EconomyKey = _ECONOMY_BY_KEY[slot_or_action]
    elif slot_or_action in ECONOMY_KEYS:
        slot = slot_or_action  # type: ignore[assignment]
    else:
        slot = "action"
    if slot == "free":
        return True
    if is_used(actor, slot):
        return False
    attr = {
        "action": "action_used",
        "bonus_action": "bonus_action_used",
        "reaction": "reaction_used",
    }[slot]
    if hasattr(actor, attr):
        setattr(actor, attr, True)
    return True


__all__ = [
    "ACTION_KEYS",
    "ECONOMY_KEYS",
    "EconomyKey",
    "consume",
    "economy_for",
    "is_used",
]

