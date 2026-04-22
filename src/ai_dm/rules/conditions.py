"""D&D 5e conditions and their mechanical effects on the rules engine.

Only the MVP conditions that influence rolls/HP are modelled. The list is
intentionally narrow — additions should land alongside the rules they touch.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Condition = Literal[
    "blinded",
    "frightened",
    "grappled",
    "incapacitated",
    "invisible",
    "paralyzed",
    "poisoned",
    "prone",
    "restrained",
    "stunned",
    "unconscious",
]

ALL_CONDITIONS: tuple[Condition, ...] = (
    "blinded",
    "frightened",
    "grappled",
    "incapacitated",
    "invisible",
    "paralyzed",
    "poisoned",
    "prone",
    "restrained",
    "stunned",
    "unconscious",
)


@dataclass(frozen=True)
class AttackModifier:
    advantage: bool = False
    disadvantage: bool = False
    auto_miss: bool = False
    auto_hit: bool = False


def attacker_mod(conditions: list[str]) -> AttackModifier:
    """How an attacker's own conditions affect their attack roll."""
    if not conditions:
        return AttackModifier()
    cs = set(conditions)
    if cs & {"blinded", "frightened", "poisoned", "prone", "restrained"}:
        return AttackModifier(disadvantage=True)
    if "invisible" in cs:
        return AttackModifier(advantage=True)
    return AttackModifier()


def target_mod(conditions: list[str]) -> AttackModifier:
    """How a target's conditions affect attacks against them."""
    if not conditions:
        return AttackModifier()
    cs = set(conditions)
    if cs & {"paralyzed", "stunned", "unconscious"}:
        # auto-crit on hit if attacker is within 5ft is omitted in MVP.
        return AttackModifier(advantage=True, auto_hit=False)
    if cs & {"blinded", "restrained"}:
        return AttackModifier(advantage=True)
    if "invisible" in cs:
        return AttackModifier(disadvantage=True)
    if "prone" in cs:
        # prone: melee attacks have advantage, ranged have disadvantage.
        # MVP: treat as advantage (melee bias).
        return AttackModifier(advantage=True)
    return AttackModifier()


def merge_advantage(*mods: AttackModifier) -> str:
    """Combine several modifiers into a single advantage state.

    Per 5e: if any source grants advantage and any other grants
    disadvantage, the roll is normal.
    """
    has_adv = any(m.advantage for m in mods)
    has_dis = any(m.disadvantage for m in mods)
    if has_adv and not has_dis:
        return "advantage"
    if has_dis and not has_adv:
        return "disadvantage"
    return "normal"

