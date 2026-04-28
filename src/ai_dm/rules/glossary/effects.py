"""Typed effects for the AI-DM rules glossary.

The :data:`Effect` discriminated union is the *engine-actionable* half of
a glossary entry; the human-readable half lives in
:attr:`~ai_dm.rules.glossary.models.GlossaryEntry.rules_text`.

Adding a new effect type:
    1. Subclass :class:`_BaseEffect` with a ``Literal[...]`` ``type`` field.
    2. Append it to the ``Effect`` ``Annotated[Union[...]]`` below.
    3. Add the corresponding branch in
       :func:`ai_dm.rules.glossary.compat.legacy_effects`.

The taxonomy intentionally mirrors the keys that the legacy
``conditions.json`` ``effects`` dict already used, so behaviour parity
with :mod:`ai_dm.rules.conditions` is mechanical.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------- #
# Base
# --------------------------------------------------------------------- #


class _BaseEffect(BaseModel):
    """Base for all glossary effect models. Frozen + extra='forbid'."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------- #
# Movement / speed
# --------------------------------------------------------------------- #


class MovementCost(_BaseEffect):
    type: Literal["movement_cost"] = "movement_cost"
    value: Union[int, Literal["crawl", "double"]]


class SpeedSetTo(_BaseEffect):
    """Speed is set to ``value`` and can't increase (e.g. grappled, prone)."""

    type: Literal["speed_set_to"] = "speed_set_to"
    value: int = Field(ge=0)


class SpeedModifier(_BaseEffect):
    type: Literal["speed_modifier"] = "speed_modifier"
    value_ft: int


# --------------------------------------------------------------------- #
# Attack rolls
# --------------------------------------------------------------------- #

_AttackRange = Literal["any", "melee_5ft", "ranged_beyond_5ft"]


class OwnAttackAdvantage(_BaseEffect):
    type: Literal["own_attack_advantage"] = "own_attack_advantage"


class OwnAttackDisadvantage(_BaseEffect):
    type: Literal["own_attack_disadvantage"] = "own_attack_disadvantage"


class AttackAdvantageAgainst(_BaseEffect):
    type: Literal["attack_advantage_against"] = "attack_advantage_against"
    range: _AttackRange = "any"


class AttackDisadvantageAgainst(_BaseEffect):
    type: Literal["attack_disadvantage_against"] = "attack_disadvantage_against"
    range: _AttackRange = "any"


class AutoCriticalHitAgainstWithin5ft(_BaseEffect):
    type: Literal["auto_critical_hit_against_within_5ft"] = (
        "auto_critical_hit_against_within_5ft"
    )


# --------------------------------------------------------------------- #
# Saving throws / ability checks
# --------------------------------------------------------------------- #

_Ability = Literal["str", "dex", "con", "int", "wis", "cha"]


class SaveAutoFail(_BaseEffect):
    type: Literal["save_auto_fail"] = "save_auto_fail"
    abilities: tuple[_Ability, ...]


class SaveAdvantage(_BaseEffect):
    type: Literal["save_advantage"] = "save_advantage"
    abilities: tuple[_Ability, ...]


class SaveDisadvantage(_BaseEffect):
    type: Literal["save_disadvantage"] = "save_disadvantage"
    abilities: tuple[_Ability, ...]


class AbilityCheckAdvantage(_BaseEffect):
    type: Literal["ability_check_advantage"] = "ability_check_advantage"


class AbilityCheckDisadvantage(_BaseEffect):
    type: Literal["ability_check_disadvantage"] = "ability_check_disadvantage"


class AutoFailCheckWithSense(_BaseEffect):
    type: Literal["auto_fail_check_with_sense"] = "auto_fail_check_with_sense"
    sense: Literal["sight", "hearing"]


# --------------------------------------------------------------------- #
# Action economy / behavioural locks
# --------------------------------------------------------------------- #


class CantTakeActions(_BaseEffect):
    type: Literal["cant_take_actions"] = "cant_take_actions"


class CantTakeBonusActions(_BaseEffect):
    type: Literal["cant_take_bonus_actions"] = "cant_take_bonus_actions"


class CantTakeReactions(_BaseEffect):
    type: Literal["cant_take_reactions"] = "cant_take_reactions"


class CantSpeak(_BaseEffect):
    type: Literal["cant_speak"] = "cant_speak"


class CantConcentrate(_BaseEffect):
    type: Literal["cant_concentrate"] = "cant_concentrate"


class CantMoveCloserToSource(_BaseEffect):
    type: Literal["cant_move_closer_to_source"] = "cant_move_closer_to_source"


class CantTarget(_BaseEffect):
    type: Literal["cant_target"] = "cant_target"
    target: Literal["charmer", "source"]


class SocialAdvantageFor(_BaseEffect):
    type: Literal["social_advantage_for"] = "social_advantage_for"
    actor: Literal["charmer", "source"]


# --------------------------------------------------------------------- #
# Damage / condition immunities
# --------------------------------------------------------------------- #


class DamageResistanceAll(_BaseEffect):
    type: Literal["damage_resistance_all"] = "damage_resistance_all"


class DamageResistance(_BaseEffect):
    type: Literal["damage_resistance"] = "damage_resistance"
    damage_types: tuple[str, ...]


class DamageImmunity(_BaseEffect):
    type: Literal["damage_immunity"] = "damage_immunity"
    damage_types: tuple[str, ...]


class DamageVulnerability(_BaseEffect):
    type: Literal["damage_vulnerability"] = "damage_vulnerability"
    damage_types: tuple[str, ...]


class ConditionImmunity(_BaseEffect):
    type: Literal["condition_immunity"] = "condition_immunity"
    conditions: tuple[str, ...]


class ImpliesCondition(_BaseEffect):
    type: Literal["implies_condition"] = "implies_condition"
    condition: str


# --------------------------------------------------------------------- #
# Exhaustion (stacking) effects
# --------------------------------------------------------------------- #


class D20PenaltyPerLevel(_BaseEffect):
    type: Literal["d20_penalty_per_level"] = "d20_penalty_per_level"
    value: int


class SpeedPenaltyPerLevelFt(_BaseEffect):
    type: Literal["speed_penalty_per_level_ft"] = "speed_penalty_per_level_ft"
    value: int


class MaxStackLevel(_BaseEffect):
    type: Literal["max_stack_level"] = "max_stack_level"
    value: int = Field(ge=1)


class DeathAtMaxStack(_BaseEffect):
    type: Literal["death_at_max_stack"] = "death_at_max_stack"


# --------------------------------------------------------------------- #
# Discriminated union
# --------------------------------------------------------------------- #


Effect = Annotated[
    Union[
        MovementCost,
        SpeedSetTo,
        SpeedModifier,
        OwnAttackAdvantage,
        OwnAttackDisadvantage,
        AttackAdvantageAgainst,
        AttackDisadvantageAgainst,
        AutoCriticalHitAgainstWithin5ft,
        SaveAutoFail,
        SaveAdvantage,
        SaveDisadvantage,
        AbilityCheckAdvantage,
        AbilityCheckDisadvantage,
        AutoFailCheckWithSense,
        CantTakeActions,
        CantTakeBonusActions,
        CantTakeReactions,
        CantSpeak,
        CantConcentrate,
        CantMoveCloserToSource,
        CantTarget,
        SocialAdvantageFor,
        DamageResistanceAll,
        DamageResistance,
        DamageImmunity,
        DamageVulnerability,
        ConditionImmunity,
        ImpliesCondition,
        D20PenaltyPerLevel,
        SpeedPenaltyPerLevelFt,
        MaxStackLevel,
        DeathAtMaxStack,
    ],
    Field(discriminator="type"),
]


__all__ = [
    "Effect",
    "MovementCost",
    "SpeedSetTo",
    "SpeedModifier",
    "OwnAttackAdvantage",
    "OwnAttackDisadvantage",
    "AttackAdvantageAgainst",
    "AttackDisadvantageAgainst",
    "AutoCriticalHitAgainstWithin5ft",
    "SaveAutoFail",
    "SaveAdvantage",
    "SaveDisadvantage",
    "AbilityCheckAdvantage",
    "AbilityCheckDisadvantage",
    "AutoFailCheckWithSense",
    "CantTakeActions",
    "CantTakeBonusActions",
    "CantTakeReactions",
    "CantSpeak",
    "CantConcentrate",
    "CantMoveCloserToSource",
    "CantTarget",
    "SocialAdvantageFor",
    "DamageResistanceAll",
    "DamageResistance",
    "DamageImmunity",
    "DamageVulnerability",
    "ConditionImmunity",
    "ImpliesCondition",
    "D20PenaltyPerLevel",
    "SpeedPenaltyPerLevelFt",
    "MaxStackLevel",
    "DeathAtMaxStack",
]

