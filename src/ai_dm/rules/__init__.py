"""Rules engine package.

Public re-exports so the rest of the codebase can do
``from ai_dm.rules import RulesEngine, DiceRoller``.
"""
from ai_dm.rules.action_resolver import ActionResolution, ActionResolver
from ai_dm.rules.attack import AttackResult, DamageResult
from ai_dm.rules.dice import DiceRoller, RollResult, roll, roll_d20
from ai_dm.rules.engine import ActorRuleState, RulesEngine
from ai_dm.rules.house_rules import HouseRule, HouseRuleSet, load_house_rules
from ai_dm.rules.skill_checks import CheckResult, make_check, skill_check

__all__ = [
    "ActionResolution",
    "ActionResolver",
    "ActorRuleState",
    "AttackResult",
    "CheckResult",
    "DamageResult",
    "DiceRoller",
    "HouseRule",
    "HouseRuleSet",
    "RollResult",
    "RulesEngine",
    "load_house_rules",
    "make_check",
    "roll",
    "roll_d20",
    "skill_check",
]

