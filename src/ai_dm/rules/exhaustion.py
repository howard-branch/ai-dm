"""SRD 5.2 (2024) one-track exhaustion.

Each level imposes ``-2`` to all d20 tests (checks, attacks, saves) and
``-5 ft`` of speed. Level 6 = death.
"""
from __future__ import annotations

from ai_dm.rules.srd_core import load

_DATA = load("exhaustion")
MAX_LEVEL: int = int(_DATA["max_level"])
DEATH_AT: int = int(_DATA["death_at"])
_PER = _DATA["per_level"]
D20_PENALTY_PER_LEVEL: int = int(_PER["d20_penalty"])
SPEED_PENALTY_PER_LEVEL_FT: int = int(_PER["speed_penalty_ft"])


def clamp(level: int) -> int:
    return max(0, min(MAX_LEVEL, int(level)))


def add(level: int, n: int = 1) -> int:
    return clamp(int(level) + int(n))


def remove(level: int, n: int = 1) -> int:
    return clamp(int(level) - int(n))


def d20_penalty(level: int) -> int:
    """Negative bonus applied to every d20 test."""
    return D20_PENALTY_PER_LEVEL * clamp(level)


def speed_penalty(level: int) -> int:
    """Negative speed adjustment in feet."""
    return SPEED_PENALTY_PER_LEVEL_FT * clamp(level)


def is_dead(level: int) -> bool:
    return clamp(level) >= DEATH_AT


__all__ = [
    "D20_PENALTY_PER_LEVEL",
    "DEATH_AT",
    "MAX_LEVEL",
    "SPEED_PENALTY_PER_LEVEL_FT",
    "add",
    "clamp",
    "d20_penalty",
    "is_dead",
    "remove",
    "speed_penalty",
]

