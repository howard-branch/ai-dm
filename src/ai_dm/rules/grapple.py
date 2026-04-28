"""Grappling and shoving — SRD 5.2.

Single source of truth: ``assets/srd5_2/core/grapple_shove.json``.

A grapple or shove is an Attack-action substitution made via a Strength
(Athletics) check contested by the target's Strength (Athletics) or
Dexterity (Acrobatics). Grappled creatures gain the ``grappled``
condition; shove either pushes the target 5 ft or knocks it
``prone``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ai_dm.rules.srd_core import load

_DATA = load("grapple_shove")
GRAPPLE = _DATA["grapple"]
SHOVE = _DATA["shove"]
MAX_SIZE_DIFF: int = int(GRAPPLE["max_size_diff"])
ATTACKER_SKILL: str = str(GRAPPLE["attacker_skill"])
DEFENDER_SKILLS: tuple[str, ...] = tuple(GRAPPLE["defender_skills"])
SHOVE_OPTIONS: tuple[str, ...] = tuple(SHOVE["options"])


def _add_condition(target: Any, key: str, *, source: str) -> None:
    """Add ``key`` to ``target.conditions`` regardless of list shape.

    Prefers :meth:`CombatantState.add_condition` (which routes through
    :class:`ConditionTracker` and respects immunities + implications);
    falls back to a plain string append for legacy ``list[str]`` actors
    used by older tests.
    """
    add = getattr(target, "add_condition", None)
    if callable(add):
        add(key, source=source)
        return
    conds = list(getattr(target, "conditions", []) or [])
    # Are we dealing with a string-only legacy list?
    if all(isinstance(c, str) for c in conds):
        if key not in conds:
            conds.append(key)
        target.conditions = conds
        return
    # Mixed / instance list: append a ConditionInstance.
    from ai_dm.game.condition_instance import ConditionInstance
    if not any(getattr(c, "key", None) == key and getattr(c, "source", None) == source
               for c in conds):
        conds.append(ConditionInstance(key=key, source=source))
    target.conditions = conds


def _remove_condition(target: Any, key: str, *, source: str | None = None) -> None:
    remove = getattr(target, "remove_condition", None)
    if callable(remove):
        remove(key, source=source)
        return
    conds = list(getattr(target, "conditions", []) or [])
    out = []
    for c in conds:
        if isinstance(c, str):
            if c == key:
                continue
        else:
            ck = getattr(c, "key", None)
            csrc = getattr(c, "source", None)
            if ck == key and (source is None or csrc == source):
                continue
        out.append(c)
    target.conditions = out

ShoveMode = Literal["push_5ft", "prone"]
_SIZE_ORDER = ["tiny", "small", "medium", "large", "huge", "gargantuan"]


def size_index(size: str | None) -> int:
    if size is None:
        return _SIZE_ORDER.index("medium")
    s = str(size).lower()
    if s not in _SIZE_ORDER:
        return _SIZE_ORDER.index("medium")
    return _SIZE_ORDER.index(s)


def size_allows(attacker: Any, target: Any) -> bool:
    """True if ``attacker`` may grapple/shove ``target`` (size diff ≤ 1)."""
    a = size_index(getattr(attacker, "size", "medium"))
    t = size_index(getattr(target, "size", "medium"))
    return (t - a) <= MAX_SIZE_DIFF


@dataclass
class GrappleResult:
    success: bool
    attacker_total: int
    defender_total: int
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "attacker_total": self.attacker_total,
            "defender_total": self.defender_total,
            "reason": self.reason,
        }


@dataclass
class ShoveResult:
    success: bool
    mode: ShoveMode
    attacker_total: int
    defender_total: int
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "mode": self.mode,
            "attacker_total": self.attacker_total,
            "defender_total": self.defender_total,
            "reason": self.reason,
        }


def _contest(
    *,
    roller: Any,
    attacker_mod: int,
    defender_mod: int,
) -> tuple[int, int]:
    a = roller.roll("1d20")
    d = roller.roll("1d20")
    return int(a.kept[0]) + int(attacker_mod), int(d.kept[0]) + int(defender_mod)


def attempt_grapple(
    attacker: Any,
    target: Any,
    *,
    roller: Any,
    attacker_mod: int = 0,
    defender_mod: int = 0,
) -> GrappleResult:
    if not size_allows(attacker, target):
        return GrappleResult(False, 0, 0, reason="target too large")
    at, dt = _contest(roller=roller, attacker_mod=attacker_mod, defender_mod=defender_mod)
    success = at > dt  # ties go to defender per SRD
    if success:
        if hasattr(target, "conditions"):
            _add_condition(target, "grappled",
                           source=getattr(attacker, "actor_id", None) or "grapple")
        if hasattr(target, "grappled_by"):
            target.grappled_by = getattr(attacker, "actor_id", None)
        if hasattr(attacker, "grappling"):
            gid = getattr(target, "actor_id", None)
            if gid and gid not in attacker.grappling:
                attacker.grappling = list(attacker.grappling) + [gid]
    return GrappleResult(success, at, dt)


def escape_grapple(
    grappled: Any,
    *,
    roller: Any,
    grappled_mod: int = 0,
    grappler_mod: int = 0,
) -> GrappleResult:
    """The grappled creature uses its action to escape (contest vs. grappler)."""
    et, gt = _contest(roller=roller, attacker_mod=grappled_mod, defender_mod=grappler_mod)
    success = et > gt
    if success and hasattr(grappled, "conditions"):
        _remove_condition(grappled, "grappled")
        if hasattr(grappled, "grappled_by"):
            grappled.grappled_by = None
    return GrappleResult(success, et, gt)


def attempt_shove(
    attacker: Any,
    target: Any,
    *,
    mode: ShoveMode = "push_5ft",
    roller: Any,
    attacker_mod: int = 0,
    defender_mod: int = 0,
) -> ShoveResult:
    if mode not in SHOVE_OPTIONS:
        return ShoveResult(False, mode, 0, 0, reason=f"unknown shove mode {mode!r}")
    if not size_allows(attacker, target):
        return ShoveResult(False, mode, 0, 0, reason="target too large")
    at, dt = _contest(roller=roller, attacker_mod=attacker_mod, defender_mod=defender_mod)
    success = at > dt
    if success and mode == "prone" and hasattr(target, "conditions"):
        _add_condition(target, "prone",
                       source=getattr(attacker, "actor_id", None) or "shove")
    return ShoveResult(success, mode, at, dt)


__all__ = [
    "ATTACKER_SKILL",
    "DEFENDER_SKILLS",
    "GrappleResult",
    "MAX_SIZE_DIFF",
    "SHOVE_OPTIONS",
    "ShoveMode",
    "ShoveResult",
    "attempt_grapple",
    "attempt_shove",
    "escape_grapple",
    "size_allows",
    "size_index",
]

