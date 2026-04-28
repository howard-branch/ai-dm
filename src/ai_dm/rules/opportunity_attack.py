"""Opportunity attacks — SRD 5.2.

Single source of truth: ``assets/srd5_2/core/opportunity_attacks.json``.

A creature provokes an opportunity attack from a hostile creature when
it leaves that creature's reach using its movement, *unless* it took
the Disengage action (``disengaging``) or is otherwise blocked
(incapacitated, speed 0).
"""
from __future__ import annotations

from typing import Any, Iterable

from ai_dm.rules.srd_core import load

_DATA = load("opportunity_attacks")
TRIGGER: str = str(_DATA["trigger"])
USES: str = str(_DATA["uses"])
BLOCKERS: tuple[str, ...] = tuple(_DATA["blockers"])


def _has_condition(actor: Any, key: str) -> bool:
    cs = getattr(actor, "conditions", None) or ()
    return key in cs


def provokes(
    mover: Any,
    *,
    mover_disengaging: bool | None = None,
) -> bool:
    """True if ``mover`` provokes an OA when leaving an enemy's reach."""
    disengaging = (
        bool(mover_disengaging)
        if mover_disengaging is not None
        else bool(getattr(mover, "disengaging", False))
    )
    if disengaging:
        return False
    if _has_condition(mover, "incapacitated"):
        # An incapacitated creature can't take actions, but it also
        # typically has speed 0 — and it certainly does not provoke an
        # OA from sitting still. We still return True so callers can
        # correctly compute whether *the defender* may react.
        return True
    return True


def can_react(defender: Any) -> bool:
    """True if ``defender`` is eligible to make an OA right now."""
    if defender is None:
        return False
    if getattr(defender, "reaction_used", False):
        return False
    if _has_condition(defender, "incapacitated"):
        return False
    if _has_condition(defender, "stunned"):
        return False
    if _has_condition(defender, "paralyzed"):
        return False
    if _has_condition(defender, "unconscious"):
        return False
    speed = int(getattr(defender, "speed", 30) or 30)
    if speed <= 0:
        return False
    return True


def eligible_reactors(mover: Any, threats: Iterable[Any]) -> list[str]:
    """Return actor_ids of ``threats`` that may react to ``mover`` leaving."""
    if not provokes(mover):
        return []
    return [
        getattr(t, "actor_id", str(t))
        for t in threats
        if can_react(t)
    ]


def consume_reaction(defender: Any) -> bool:
    """Spend the defender's reaction; ``False`` if already spent."""
    if getattr(defender, "reaction_used", False):
        return False
    if hasattr(defender, "reaction_used"):
        defender.reaction_used = True
    return True


__all__ = [
    "BLOCKERS",
    "TRIGGER",
    "USES",
    "can_react",
    "consume_reaction",
    "eligible_reactors",
    "provokes",
]

