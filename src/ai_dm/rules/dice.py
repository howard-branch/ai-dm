"""Dice expression parser + roller.

Supports the standard D&D notation::

    "1d20"       single d20
    "2d6+3"      sum of two d6 plus modifier
    "1d8-1"      negative modifier
    "4d6kh3"     keep highest 3 (advantage on stats)
    "2d20kh1"    advantage
    "2d20kl1"    disadvantage

The :class:`DiceRoller` is seedable for deterministic tests.
``roll_d20`` is preserved as a backwards-compatible helper.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Literal

# 2d20kh1, 4d6kl1, 1d8+3, 2d6-1, d20
_EXPR_RE = re.compile(
    r"""^\s*
        (?P<count>\d*)d(?P<sides>\d+)
        (?:\s*(?P<keep>k[hl])\s*(?P<keep_n>\d+))?
        (?:\s*(?P<sign>[+-])\s*(?P<modifier>\d+))?
    \s*$""",
    re.VERBOSE | re.IGNORECASE,
)

Advantage = Literal["normal", "advantage", "disadvantage"]


@dataclass
class RollResult:
    expression: str
    rolls: list[int] = field(default_factory=list)
    kept: list[int] = field(default_factory=list)
    modifier: int = 0
    total: int = 0
    advantage: Advantage = "normal"
    crit: bool = False  # natural 20 on a single d20
    fumble: bool = False  # natural 1 on a single d20

    def to_dict(self) -> dict:
        return {
            "expression": self.expression,
            "rolls": list(self.rolls),
            "kept": list(self.kept),
            "modifier": self.modifier,
            "total": self.total,
            "advantage": self.advantage,
            "crit": self.crit,
            "fumble": self.fumble,
        }


class DiceRoller:
    """Seedable dice roller."""

    def __init__(
        self,
        *,
        seed: int | None = None,
        rng: random.Random | None = None,
    ) -> None:
        if rng is not None:
            self.rng = rng
        elif seed is not None:
            self.rng = random.Random(seed)
        else:
            self.rng = random.Random()

    def roll(
        self,
        expression: str,
        *,
        advantage: Advantage = "normal",
    ) -> RollResult:
        m = _EXPR_RE.match(expression)
        if not m:
            raise ValueError(f"invalid dice expression: {expression!r}")

        count = int(m.group("count") or "1")
        sides = int(m.group("sides"))
        keep = (m.group("keep") or "").lower()
        keep_n = int(m.group("keep_n")) if m.group("keep_n") else None
        modifier = 0
        if m.group("modifier"):
            modifier = int(m.group("modifier"))
            if m.group("sign") == "-":
                modifier = -modifier

        if count <= 0 or sides <= 0:
            raise ValueError(f"non-positive dice in {expression!r}")

        if (
            advantage in ("advantage", "disadvantage")
            and sides == 20
            and count == 1
            and not keep
        ):
            count = 2
            keep = "kh" if advantage == "advantage" else "kl"
            keep_n = 1

        rolls = [self.rng.randint(1, sides) for _ in range(count)]
        kept = self._apply_keep(rolls, keep, keep_n)
        total = sum(kept) + modifier

        single_d20 = sides == 20 and len(kept) == 1
        crit = single_d20 and kept[0] == 20
        fumble = single_d20 and kept[0] == 1

        return RollResult(
            expression=expression,
            rolls=rolls,
            kept=kept,
            modifier=modifier,
            total=total,
            advantage=advantage,
            crit=crit,
            fumble=fumble,
        )

    @staticmethod
    def _apply_keep(rolls: list[int], keep: str, keep_n: int | None) -> list[int]:
        if not keep or keep_n is None:
            return list(rolls)
        ordered = sorted(rolls, reverse=(keep == "kh"))
        return ordered[: max(0, min(keep_n, len(rolls)))]


# ---- Backwards-compat helpers (used by old tests and combat_machine) ---- #

_DEFAULT_ROLLER = DiceRoller()


def roll_d20() -> int:
    """Simple d20 roll using the module-level RNG."""
    return _DEFAULT_ROLLER.rng.randint(1, 20)


def roll(expression: str, *, advantage: Advantage = "normal") -> RollResult:
    """Convenience wrapper around the module roller."""
    return _DEFAULT_ROLLER.roll(expression, advantage=advantage)


# --------------------------------------------------------------------- #
# Advantage / disadvantage stacking and unified d20 test
# --------------------------------------------------------------------- #


def combine_advantage(adv_sources: int, dis_sources: int) -> Advantage:
    """Combine N sources of advantage and M of disadvantage.

    Per SRD: any of each cancels to ``"normal"``; otherwise pick the
    side with at least one source.
    """
    a = max(0, int(adv_sources))
    d = max(0, int(dis_sources))
    if a > 0 and d > 0:
        return "normal"
    if a > 0:
        return "advantage"
    if d > 0:
        return "disadvantage"
    return "normal"


@dataclass
class D20Test:
    """Result of a unified d20 test (check / save / attack)."""
    roll: int               # the natural die kept after adv/dis
    modifier: int
    total: int
    advantage: Advantage
    crit: bool              # natural 20
    fumble: bool            # natural 1
    dc: int | None = None
    target: int | None = None  # AC for attacks; same as ``dc`` for saves/checks
    success: bool | None = None
    raw: RollResult | None = None

    def to_dict(self) -> dict:
        return {
            "roll": self.roll,
            "modifier": self.modifier,
            "total": self.total,
            "advantage": self.advantage,
            "crit": self.crit,
            "fumble": self.fumble,
            "dc": self.dc,
            "target": self.target,
            "success": self.success,
        }


def d20_test(
    roller: DiceRoller,
    *,
    modifier: int = 0,
    dc: int | None = None,
    ac: int | None = None,
    advantage: Advantage = "normal",
    is_attack: bool = False,
) -> D20Test:
    """Unified d20 test: ability check, saving throw, or attack roll.

    * If ``is_attack`` (or ``ac`` is given): nat 20 = auto-hit, nat 1 =
      auto-miss; otherwise compare ``total`` vs ``ac``.
    * Else compare ``total`` vs ``dc`` (no nat-20/1 auto for checks).
    """
    rr = roller.roll("1d20", advantage=advantage)
    nat = int(rr.kept[0])
    total = nat + int(modifier)
    target = ac if ac is not None else dc
    success: bool | None = None
    if is_attack or ac is not None:
        if rr.crit:
            success = True
        elif rr.fumble:
            success = False
        elif target is not None:
            success = total >= target
    elif dc is not None:
        success = total >= dc
    return D20Test(
        roll=nat,
        modifier=int(modifier),
        total=total,
        advantage=advantage,
        crit=rr.crit,
        fumble=rr.fumble,
        dc=dc,
        target=target,
        success=success,
        raw=rr,
    )

