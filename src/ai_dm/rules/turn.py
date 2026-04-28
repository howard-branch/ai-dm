"""Turn structure — SRD 5.2.

Single source of truth: ``assets/srd5_2/core/turn_structure.json``.

Each turn: start_of_turn → action / movement / bonus_action (in any
order) → end_of_turn. A creature additionally has 1 reaction (resets
at start of own turn) and 1 free object interaction per turn.
"""
from __future__ import annotations

from typing import Any, Literal

from ai_dm.rules.srd_core import load

_DATA = load("turn_structure")

TurnPhase = Literal[
    "start_of_turn", "action", "movement", "bonus_action", "end_of_turn"
]
PHASES: tuple[TurnPhase, ...] = tuple(_DATA["phases"])  # type: ignore[assignment]
REACTION_RESETS_AT: str = str(_DATA["reaction_resets_at"])
FREE_OBJECT_INTERACTIONS_PER_TURN: int = int(_DATA["free_object_interactions_per_turn"])


def start_of_turn(actor: Any, round_no: int | None = None) -> None:
    """Reset per-turn state on ``actor``. Idempotent."""
    if actor is None:
        return
    if hasattr(actor, "start_of_turn"):
        actor.start_of_turn()
        return
    for attr in ("action_used", "bonus_action_used"):
        if hasattr(actor, attr):
            setattr(actor, attr, False)
    if hasattr(actor, "movement_used"):
        actor.movement_used = 0
    if REACTION_RESETS_AT == "start_of_turn" and hasattr(actor, "reaction_used"):
        actor.reaction_used = False
    if hasattr(actor, "free_interactions_used"):
        actor.free_interactions_used = 0


def end_of_turn(actor: Any) -> dict[str, Any]:
    """Hook fired after the actor explicitly ends its turn.

    Returns a small report describing what was cleaned up — callers
    may publish it on the event bus.
    """
    report: dict[str, Any] = {"actor_id": getattr(actor, "actor_id", None)}
    if actor is None:
        return report
    # Tick down readied action: it expires at the start of the actor's
    # next turn, but if not consumed by then it's already cleared by
    # CombatantState.start_of_turn — nothing to do here.
    return report


def free_interactions_remaining(actor: Any) -> int:
    used = int(getattr(actor, "free_interactions_used", 0) or 0)
    return max(0, FREE_OBJECT_INTERACTIONS_PER_TURN - used)


__all__ = [
    "FREE_OBJECT_INTERACTIONS_PER_TURN",
    "PHASES",
    "REACTION_RESETS_AT",
    "TurnPhase",
    "end_of_turn",
    "free_interactions_remaining",
    "start_of_turn",
]

