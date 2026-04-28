"""Carrying capacity & encumbrance.

Single source of truth: ``assets/srd5_2/core/encumbrance.json``.

Default rule: capacity = 15 × STR (lb); push/drag/lift = 30 × STR.
Variant rule (DMG): encumbered at 5 × STR (-10 ft speed), heavily
encumbered at 10 × STR (-20 ft speed + disadvantage on STR/DEX/CON
checks, attacks and saves).
"""
from __future__ import annotations

from typing import Literal

from ai_dm.rules.srd_core import load

_DATA = load("encumbrance")
_VARIANT = _DATA["variant"]

CARRYING_CAPACITY_PER_STR: int = int(_DATA["carrying_capacity_per_str"])
PUSH_DRAG_LIFT_PER_STR: int = int(_DATA["push_drag_lift_per_str"])

ENCUMBERED_PER_STR: int = int(_VARIANT["encumbered_per_str"])
HEAVILY_ENCUMBERED_PER_STR: int = int(_VARIANT["heavily_encumbered_per_str"])
MAX_PER_STR_VARIANT: int = int(_VARIANT["max_per_str"])
ENCUMBERED_SPEED_FT: int = int(_VARIANT["encumbered_speed_penalty_ft"])
HEAVILY_ENCUMBERED_SPEED_FT: int = int(_VARIANT["heavily_encumbered_speed_penalty_ft"])
HEAVILY_ENCUMBERED_DISADVANTAGE: tuple[str, ...] = tuple(_VARIANT["heavily_encumbered_disadvantage"])

EncumbranceStatus = Literal["normal", "encumbered", "heavy"]


def carrying_capacity(strength_score: int) -> int:
    return int(strength_score) * CARRYING_CAPACITY_PER_STR


def push_drag_lift(strength_score: int) -> int:
    return int(strength_score) * PUSH_DRAG_LIFT_PER_STR


def encumbrance_status(
    total_weight_lb: float,
    strength_score: int,
    *,
    variant: bool = False,
) -> EncumbranceStatus:
    """Return ``"normal"``, ``"encumbered"`` or ``"heavy"``.

    With ``variant=False`` only ``"normal"`` and ``"heavy"`` are
    possible: anything past 15 × STR is overburdened. The variant rule
    introduces the middle ``"encumbered"`` band at 5 × STR.
    """
    s = int(strength_score)
    w = float(total_weight_lb)
    if variant:
        if w > HEAVILY_ENCUMBERED_PER_STR * s:
            return "heavy"
        if w > ENCUMBERED_PER_STR * s:
            return "encumbered"
        return "normal"
    if w > CARRYING_CAPACITY_PER_STR * s:
        return "heavy"
    return "normal"


def speed_penalty(status: EncumbranceStatus) -> int:
    """Speed delta in ft (≤ 0). Default rule: 0 unless 'heavy' (variant)."""
    if status == "encumbered":
        return ENCUMBERED_SPEED_FT
    if status == "heavy":
        return HEAVILY_ENCUMBERED_SPEED_FT
    return 0


def imposes_disadvantage(status: EncumbranceStatus) -> tuple[str, ...]:
    """Returns the SRD list of d20 categories that have disadvantage at this status."""
    if status == "heavy":
        return HEAVILY_ENCUMBERED_DISADVANTAGE
    return ()


__all__ = [
    "CARRYING_CAPACITY_PER_STR",
    "ENCUMBERED_PER_STR",
    "ENCUMBERED_SPEED_FT",
    "EncumbranceStatus",
    "HEAVILY_ENCUMBERED_DISADVANTAGE",
    "HEAVILY_ENCUMBERED_PER_STR",
    "HEAVILY_ENCUMBERED_SPEED_FT",
    "MAX_PER_STR_VARIANT",
    "PUSH_DRAG_LIFT_PER_STR",
    "carrying_capacity",
    "encumbrance_status",
    "imposes_disadvantage",
    "push_drag_lift",
    "speed_penalty",
]

