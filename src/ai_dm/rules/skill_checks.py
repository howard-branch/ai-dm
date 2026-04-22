"""Ability checks and saving throws."""
from __future__ import annotations

from dataclasses import dataclass

from ai_dm.rules.dice import DiceRoller, RollResult, roll_d20


def skill_check(modifier: int, dc: int) -> dict:
    """Legacy stub kept so older callers/tests keep working."""
    roll = roll_d20()
    total = roll + modifier
    return {
        "roll": roll,
        "modifier": modifier,
        "total": total,
        "success": total >= dc,
    }


@dataclass
class CheckResult:
    roll: RollResult
    modifier: int
    dc: int
    total: int
    success: bool
    crit: bool
    fumble: bool

    def to_dict(self) -> dict:
        return {
            "roll": self.roll.to_dict(),
            "modifier": self.modifier,
            "dc": self.dc,
            "total": self.total,
            "success": self.success,
            "crit": self.crit,
            "fumble": self.fumble,
        }


def make_check(
    roller: DiceRoller,
    *,
    modifier: int,
    dc: int,
    advantage: str = "normal",
) -> CheckResult:
    roll = roller.roll("1d20", advantage=advantage)  # type: ignore[arg-type]
    total = roll.total + modifier
    return CheckResult(
        roll=roll,
        modifier=modifier,
        dc=dc,
        total=total,
        success=total >= dc,
        crit=roll.crit,
        fumble=roll.fumble,
    )

