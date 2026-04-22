"""Attack and damage resolution."""
from __future__ import annotations

from dataclasses import dataclass, field

from ai_dm.rules.dice import DiceRoller, RollResult


@dataclass
class AttackResult:
    attack_roll: RollResult
    attack_modifier: int
    target_ac: int
    total: int
    hit: bool
    crit: bool
    fumble: bool

    def to_dict(self) -> dict:
        return {
            "attack_roll": self.attack_roll.to_dict(),
            "attack_modifier": self.attack_modifier,
            "target_ac": self.target_ac,
            "total": self.total,
            "hit": self.hit,
            "crit": self.crit,
            "fumble": self.fumble,
        }


@dataclass
class DamageResult:
    rolls: list[RollResult] = field(default_factory=list)
    bonus: int = 0
    damage_type: str = "untyped"
    total: int = 0
    crit: bool = False

    def to_dict(self) -> dict:
        return {
            "rolls": [r.to_dict() for r in self.rolls],
            "bonus": self.bonus,
            "damage_type": self.damage_type,
            "total": self.total,
            "crit": self.crit,
        }


def make_attack(
    roller: DiceRoller,
    *,
    attack_modifier: int,
    target_ac: int,
    advantage: str = "normal",
) -> AttackResult:
    roll = roller.roll("1d20", advantage=advantage)  # type: ignore[arg-type]
    total = roll.total + attack_modifier
    # 5e: nat 20 always hits; nat 1 always misses.
    if roll.crit:
        hit = True
    elif roll.fumble:
        hit = False
    else:
        hit = total >= target_ac
    return AttackResult(
        attack_roll=roll,
        attack_modifier=attack_modifier,
        target_ac=target_ac,
        total=total,
        hit=hit,
        crit=roll.crit,
        fumble=roll.fumble,
    )


def roll_damage(
    roller: DiceRoller,
    *,
    dice: str,
    bonus: int = 0,
    damage_type: str = "untyped",
    crit: bool = False,
    crit_doubles_dice: bool = True,
) -> DamageResult:
    """Roll damage dice. On a crit (and ``crit_doubles_dice=True``) double the dice rolled."""
    primary = roller.roll(dice)
    rolls = [primary]
    total = primary.total
    if crit and crit_doubles_dice:
        # Roll the same dice expression again (without the modifier — it's
        # baked into ``primary.total`` already, and ``bonus`` is added once).
        # Strip any +/- mod from the expression for the second roll.
        bare = dice.split("+")[0].split("-")[0].strip()
        extra = roller.roll(bare)
        rolls.append(extra)
        total += extra.total
    total += bonus
    return DamageResult(
        rolls=rolls,
        bonus=bonus,
        damage_type=damage_type,
        total=max(0, total),
        crit=crit,
    )


def apply_resistance(
    amount: int,
    damage_type: str,
    *,
    resistances: list[str] | None = None,
    vulnerabilities: list[str] | None = None,
    immunities: list[str] | None = None,
) -> int:
    if amount <= 0:
        return 0
    immunities = immunities or []
    if damage_type in immunities:
        return 0
    resistances = resistances or []
    vulnerabilities = vulnerabilities or []
    if damage_type in vulnerabilities and damage_type in resistances:
        # 5e: if both, they cancel.
        return amount
    if damage_type in resistances:
        return amount // 2
    if damage_type in vulnerabilities:
        return amount * 2
    return amount

