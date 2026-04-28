"""SRD-style XP budget / encounter difficulty.

Implements the DMG XP-threshold and group-multiplier tables: classify
an encounter as ``easy``/``medium``/``hard``/``deadly`` against a
party's pooled thresholds, compute the adjusted XP after the group-size
multiplier, and award XP from defeated foes.

Single source of truth: ``assets/srd5_2/core/xp_budget.json``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

from ai_dm.rules.srd_core import load

Difficulty = Literal["easy", "medium", "hard", "deadly"]
DIFFICULTIES: tuple[Difficulty, ...] = ("easy", "medium", "hard", "deadly")


@dataclass(frozen=True)
class PartyThresholds:
    level: int
    easy: int
    medium: int
    hard: int
    deadly: int


@dataclass(frozen=True)
class GroupSizeMultiplier:
    min_size: int
    max_size: int
    mult: float


_DATA = load("xp_budget")
_THRESHOLDS: dict[int, PartyThresholds] = {
    int(lvl): PartyThresholds(
        level=int(lvl),
        easy=int(row["easy"]),
        medium=int(row["medium"]),
        hard=int(row["hard"]),
        deadly=int(row["deadly"]),
    )
    for lvl, row in _DATA["thresholds_by_level"].items()
}
_MULTS: tuple[GroupSizeMultiplier, ...] = tuple(
    GroupSizeMultiplier(int(r["min"]), int(r["max"]), float(r["mult"]))
    for r in _DATA["encounter_multipliers"]
)
_LEVEL_XP: dict[int, int] = {int(k): int(v) for k, v in _DATA["level_xp_table"].items()}


def thresholds_for_level(level: int) -> PartyThresholds:
    """Return DMG XP thresholds for a single character level."""
    lvl = max(1, min(20, int(level)))
    return _THRESHOLDS[lvl]


def party_thresholds(levels: Sequence[int]) -> dict[Difficulty, int]:
    """Sum thresholds across every PC's level."""
    out: dict[Difficulty, int] = {d: 0 for d in DIFFICULTIES}
    for lvl in levels:
        t = thresholds_for_level(lvl)
        out["easy"] += t.easy
        out["medium"] += t.medium
        out["hard"] += t.hard
        out["deadly"] += t.deadly
    return out


def group_multiplier(num_monsters: int) -> float:
    """Encounter XP multiplier based on the number of foes."""
    n = max(1, int(num_monsters))
    for row in _MULTS:
        if row.min_size <= n <= row.max_size:
            return row.mult
    return _MULTS[-1].mult


def adjusted_xp(monster_xps: Sequence[int], party_size: int) -> int:
    """Sum monster XP × group multiplier (party-size-aware)."""
    base = sum(int(x) for x in monster_xps if x)
    if base == 0:
        return 0
    mult = group_multiplier(len(list(monster_xps)))
    # Per DMG: a small (≤2) party bumps the multiplier up one row;
    # a large (≥6) party bumps it down one row.
    rows = list(_MULTS)
    idx = next(
        (i for i, r in enumerate(rows) if r.min_size <= len(list(monster_xps)) <= r.max_size),
        len(rows) - 1,
    )
    if int(party_size) < 3:
        idx = min(len(rows) - 1, idx + 1)
    elif int(party_size) >= 6:
        idx = max(0, idx - 1)
    mult = rows[idx].mult
    return int(round(base * mult))


def classify_encounter(
    monster_xps: Sequence[int], party_levels: Sequence[int]
) -> Difficulty:
    """Compare adjusted XP against pooled party thresholds."""
    adj = adjusted_xp(monster_xps, party_size=len(list(party_levels)))
    thresh = party_thresholds(party_levels)
    # Walk descending; first threshold met wins.
    if adj >= thresh["deadly"]:
        return "deadly"
    if adj >= thresh["hard"]:
        return "hard"
    if adj >= thresh["medium"]:
        return "medium"
    return "easy"


def xp_for_level(level: int) -> int:
    """Cumulative XP required to *be* at ``level``."""
    return _LEVEL_XP[max(1, min(20, int(level)))]


def xp_to_next_level(current_xp: int, current_level: int) -> int:
    """XP remaining until the next level (0 if already at the cap)."""
    if current_level >= 20:
        return 0
    return max(0, xp_for_level(current_level + 1) - int(current_xp))


def level_for_xp(xp: int) -> int:
    """Highest level whose threshold is ≤ ``xp``."""
    out = 1
    for lvl in sorted(_LEVEL_XP):
        if int(xp) >= _LEVEL_XP[lvl]:
            out = lvl
    return out


def award_xp(party_member_ids: Sequence[str], defeated_xp: int) -> dict[str, int]:
    """Split ``defeated_xp`` evenly across living party members.

    Returns ``{actor_id: xp_share}``. Remainder is distributed
    deterministically to the first members.
    """
    members = list(party_member_ids)
    if not members or defeated_xp <= 0:
        return {m: 0 for m in members}
    base = defeated_xp // len(members)
    rem = defeated_xp - base * len(members)
    out: dict[str, int] = {}
    for i, m in enumerate(members):
        out[m] = base + (1 if i < rem else 0)
    return out


def total_xp_from_monsters(monster_xps: Iterable[int]) -> int:
    """Plain sum (no multiplier) — use this for *awarded* XP per SRD."""
    return sum(int(x) for x in monster_xps if x)


__all__ = [
    "Difficulty",
    "DIFFICULTIES",
    "GroupSizeMultiplier",
    "PartyThresholds",
    "adjusted_xp",
    "award_xp",
    "classify_encounter",
    "group_multiplier",
    "level_for_xp",
    "party_thresholds",
    "thresholds_for_level",
    "total_xp_from_monsters",
    "xp_for_level",
    "xp_to_next_level",
]

