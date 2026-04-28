"""SRD 5.2.1 magic-item attunement bookkeeping.

Single source of truth: ``assets/srd5_2/core/attunement.json``.
"""
from __future__ import annotations

from ai_dm.rules.srd_core import load

_DATA = load("attunement")

MAX_ATTUNED: int = int(_DATA["max_attuned"])
SHORT_REST_TO_ATTUNE_MIN: int = int(_DATA["short_rest_to_attune_min"])


def can_attune(currently_attuned: list[str] | tuple[str, ...]) -> bool:
    """True when the actor has fewer than :data:`MAX_ATTUNED` attunements."""
    return len(currently_attuned) < MAX_ATTUNED


__all__ = ["MAX_ATTUNED", "SHORT_REST_TO_ATTUNE_MIN", "can_attune"]

