"""Hiding & invisibility — SRD 5.2.

Single source of truth: ``assets/srd5_2/core/stealth.json``.

A successful Hide leaves the creature ``hidden`` (unseen and unheard).
Hiding is broken by attacking, casting a spell, speaking loudly, or
leaving the cover that hid them. An ``invisible`` attacker has
advantage on attack rolls; attackers targeting an invisible creature
have disadvantage. Both effects roll up to a single per-attack
``advantage`` / ``disadvantage`` flag.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_dm.rules.srd_core import load

_DATA = load("stealth")
BREAK_TRIGGERS: tuple[str, ...] = tuple(_DATA["break_triggers"])
INVISIBLE_GRANTS_ATTACKER_ADVANTAGE: bool = bool(
    _DATA["invisible_grants"]["attacker_advantage"]
)
INVISIBLE_GRANTS_TARGET_DISADVANTAGE: bool = bool(
    _DATA["invisible_grants"]["target_disadvantage"]
)
UNSEEN_ATTACKER_ADVANTAGE: bool = bool(_DATA["unseen_attacker_advantage"])


@dataclass
class HideResult:
    success: bool
    roll: int | None
    total: int | None
    dc: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "roll": self.roll,
            "total": self.total,
            "dc": self.dc,
        }


def attempt_hide(
    *,
    roller: Any,
    stealth_modifier: int = 0,
    dc: int = 10,
) -> HideResult:
    """Roll Stealth vs. ``dc`` (typically the highest passive Perception)."""
    rr = roller.roll("1d20")
    nat = int(rr.kept[0])
    total = nat + int(stealth_modifier)
    return HideResult(success=total >= dc, roll=nat, total=total, dc=int(dc))


def breaks_on(action_kind: str) -> bool:
    """True if performing ``action_kind`` should clear ``hidden``."""
    return action_kind in BREAK_TRIGGERS


def maybe_break(actor: Any, action_kind: str) -> bool:
    """If ``action_kind`` would break stealth, clear ``hidden`` on actor."""
    if not getattr(actor, "hidden", False):
        return False
    if not breaks_on(action_kind):
        return False
    actor.hidden = False
    return True


def attack_advantage(
    *,
    attacker_invisible: bool = False,
    attacker_unseen: bool = False,
    target_invisible: bool = False,
    target_unseen: bool = False,
) -> str:
    """Combine sight conditions into an ``"advantage"|"disadvantage"|"normal"`` flag."""
    has_adv = (
        (attacker_invisible and INVISIBLE_GRANTS_ATTACKER_ADVANTAGE)
        or (attacker_unseen and UNSEEN_ATTACKER_ADVANTAGE)
    )
    has_dis = (
        (target_invisible and INVISIBLE_GRANTS_TARGET_DISADVANTAGE)
        or target_unseen
    )
    if has_adv and not has_dis:
        return "advantage"
    if has_dis and not has_adv:
        return "disadvantage"
    return "normal"


__all__ = [
    "BREAK_TRIGGERS",
    "INVISIBLE_GRANTS_ATTACKER_ADVANTAGE",
    "INVISIBLE_GRANTS_TARGET_DISADVANTAGE",
    "UNSEEN_ATTACKER_ADVANTAGE",
    "HideResult",
    "attack_advantage",
    "attempt_hide",
    "breaks_on",
    "maybe_break",
]

