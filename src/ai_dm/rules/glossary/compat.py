"""Compatibility shim between the typed glossary and the legacy flat
``effects`` dict that :mod:`ai_dm.rules.conditions` (and tests) still
read.

The shim has two responsibilities:

* :func:`legacy_effects` — given a condition key, return the same flat
  dict shape the catalog used pre-glossary, regardless of whether the
  underlying record is the new typed form or the still-untouched legacy
  one.
* :func:`synthesise_legacy` — translate a tuple of typed
  :data:`~ai_dm.rules.glossary.effects.Effect` records into that flat
  dict (used by the synthesiser and tests).

Keeping the synthesiser pure / pickleable lets call sites cache results.
"""
from __future__ import annotations

from typing import Any, Iterable

from . import effects as E
from .registry import registry


# --------------------------------------------------------------------- #
# Typed → flat dict
# --------------------------------------------------------------------- #


def synthesise_legacy(effects_iter: Iterable[E.Effect]) -> dict[str, Any]:
    """Translate typed effects back into the flat-dict shape used by
    :mod:`ai_dm.rules.conditions` before the glossary refactor."""
    out: dict[str, Any] = {}
    for ef in effects_iter:
        if isinstance(ef, E.OwnAttackAdvantage):
            out["attacker_advantage"] = True
        elif isinstance(ef, E.OwnAttackDisadvantage):
            out["attacker_disadvantage"] = True
        elif isinstance(ef, E.AttackAdvantageAgainst):
            if ef.range == "any":
                out["target_advantage"] = True
            elif ef.range == "melee_5ft":
                out["target_advantage_melee"] = True
            elif ef.range == "ranged_beyond_5ft":
                out["target_advantage_ranged"] = True
        elif isinstance(ef, E.AttackDisadvantageAgainst):
            if ef.range == "any":
                out["target_disadvantage"] = True
            elif ef.range == "melee_5ft":
                out["target_disadvantage_melee"] = True
            elif ef.range == "ranged_beyond_5ft":
                out["target_disadvantage_ranged"] = True
        elif isinstance(ef, E.AutoCriticalHitAgainstWithin5ft):
            out["attacks_within_5ft_crit"] = True
        elif isinstance(ef, E.SpeedSetTo) and ef.value == 0:
            out["speed_zero"] = True
        elif isinstance(ef, E.MovementCost):
            out["movement_cost"] = ef.value
        elif isinstance(ef, E.SaveAutoFail):
            out["auto_fail_saves"] = list(ef.abilities)
        elif isinstance(ef, E.SaveAdvantage):
            for a in ef.abilities:
                out[f"{a}_save_advantage"] = True
        elif isinstance(ef, E.SaveDisadvantage):
            for a in ef.abilities:
                out[f"{a}_save_disadvantage"] = True
        elif isinstance(ef, E.AbilityCheckAdvantage):
            out["ability_check_advantage"] = True
        elif isinstance(ef, E.AbilityCheckDisadvantage):
            out["ability_check_disadvantage"] = True
        elif isinstance(ef, E.AutoFailCheckWithSense):
            out[f"auto_fail_{ef.sense}_checks"] = True
        elif isinstance(ef, E.CantTakeActions):
            out["no_actions"] = True
        elif isinstance(ef, E.CantTakeBonusActions):
            out["no_bonus_actions"] = True
        elif isinstance(ef, E.CantTakeReactions):
            out["no_reactions"] = True
        elif isinstance(ef, E.CantMoveCloserToSource):
            out["cant_move_closer_to_source"] = True
        elif isinstance(ef, E.CantTarget) and ef.target == "charmer":
            out["cant_attack_charmer"] = True
        elif isinstance(ef, E.SocialAdvantageFor) and ef.actor == "charmer":
            out["charmer_advantage_on_social"] = True
        elif isinstance(ef, E.DamageResistanceAll):
            out["resistance_all_damage"] = True
        elif isinstance(ef, E.ConditionImmunity):
            # Petrified: immune to poisoned (+ disease, encoded as a tag).
            if "poisoned" in ef.conditions:
                out["immune_to_poison_and_disease"] = True
        elif isinstance(ef, E.ImpliesCondition):
            if ef.condition == "incapacitated":
                out["incapacitated_implied"] = True
            elif ef.condition == "prone":
                out["prone_implied"] = True
        # Exhaustion stacking effects.
        elif isinstance(ef, E.D20PenaltyPerLevel):
            out["d20_penalty_per_level"] = ef.value
        elif isinstance(ef, E.SpeedPenaltyPerLevelFt):
            out["speed_penalty_per_level_ft"] = ef.value
        elif isinstance(ef, E.MaxStackLevel):
            out["max_level"] = ef.value
        elif isinstance(ef, E.DeathAtMaxStack):
            out["death_at_max"] = True
    return out


# --------------------------------------------------------------------- #
# Public lookup
# --------------------------------------------------------------------- #


def legacy_effects(condition_key: str) -> dict[str, Any]:
    """Return the legacy flat-dict ``effects`` for a condition.

    Lookup order:

    1. If the catalog row has typed ``effects`` — synthesise from them.
    2. Else if it has ``effects_legacy`` — return a copy.
    3. Else return ``{}``.
    """
    raw = registry.raw_record("conditions", condition_key)
    if raw is None:
        return {}
    typed = raw.get("effects")
    if isinstance(typed, list) and typed:
        # Validate-and-rebuild via the registry's typed entry to catch drift.
        entry = registry.get_condition(condition_key)
        if entry is not None and entry.effects:
            return synthesise_legacy(entry.effects)
    legacy = raw.get("effects_legacy")
    if isinstance(legacy, dict):
        return dict(legacy)
    # Last-ditch: pre-migration files may still expose the old shape under
    # plain ``effects`` (a dict, not a list).
    plain = raw.get("effects")
    if isinstance(plain, dict):
        return dict(plain)
    return {}


__all__ = ["legacy_effects", "synthesise_legacy"]

