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
