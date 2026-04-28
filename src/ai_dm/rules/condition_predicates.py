"""Stateless derived predicates over a set of active conditions.

These let the rules engine, planner, action resolver, and UI ask
"can this actor take an action right now?" without each call site
re-implementing the SRD effect-key glue.

Every function accepts the same heterogeneous shape used elsewhere
in the engine — a mixed iterable of SRD strings,
:class:`ai_dm.game.condition_instance.ConditionInstance` objects, or
dicts with a ``key`` field — by going through
:func:`ai_dm.rules.conditions.implied`.
"""
from __future__ import annotations

from typing import Any, Iterable

from ai_dm.rules.conditions import _EFFECTS, _has_any, implied


def _has(conditions: Iterable[Any], key: str) -> bool:
    return _has_any(implied(conditions), key)


# --------------------------------------------------------------------- #
# Action economy
# --------------------------------------------------------------------- #


def can_take_actions(conditions: Iterable[Any]) -> bool:
    """False when any active condition forbids actions (incapacitated etc.)."""
    return not _has(conditions, "no_actions")


def can_take_bonus_action(conditions: Iterable[Any]) -> bool:
    return not _has(conditions, "no_bonus_actions")


def can_take_reaction(conditions: Iterable[Any]) -> bool:
    return not _has(conditions, "no_reactions")


def can_speak(conditions: Iterable[Any]) -> bool:
    """Speech-gated effects (verbal spell components, shouted orders).

    Stunned / unconscious / paralyzed all suppress speech via SRD; we
    expose it as a single predicate so callers don't repeat the list.
    """
    return not _has(conditions, "no_speech") and not _has(conditions, "no_actions")


# --------------------------------------------------------------------- #
# Movement
# --------------------------------------------------------------------- #


def speed_zero(conditions: Iterable[Any]) -> bool:
    return _has(conditions, "speed_zero")


# --------------------------------------------------------------------- #
# Saves & checks
# --------------------------------------------------------------------- #


def auto_fail_str_dex_saves(conditions: Iterable[Any]) -> bool:
    """SRD: paralyzed / petrified / stunned / unconscious all auto-fail Str + Dex saves."""
    keys = implied(conditions)
    for c in keys:
        ab = _EFFECTS.get(c, {}).get("auto_fail_saves") or ()
        if "str" in ab and "dex" in ab:
            return True
    return False


def ability_check_disadvantage(conditions: Iterable[Any]) -> bool:
    return _has(conditions, "ability_check_disadvantage")


# --------------------------------------------------------------------- #
# Attack maths (caller-relative)
# --------------------------------------------------------------------- #


def crit_within_5ft(conditions: Iterable[Any]) -> bool:
    return _has(conditions, "attacks_within_5ft_crit")


def cannot_attack(attacker_conditions: Iterable[Any], *, target_id: str | None = None,
                  charmer_id: str | None = None) -> bool:
    """Charmed creatures can't attack the charmer; everyone else is fair game.

    ``target_id`` / ``charmer_id`` are optional — without both we can't
    decide and conservatively answer ``False`` (i.e. allow the attack).
    """
    if not _has(attacker_conditions, "cant_attack_charmer"):
        return False
    if target_id is None or charmer_id is None:
        return False
    return target_id == charmer_id


def cannot_move_closer_to(actor_conditions: Iterable[Any], *, source_id: str | None,
                          fear_source_id: str | None) -> bool:
    """Frightened creatures can't willingly move closer to the source of fear."""
    if not _has(actor_conditions, "cant_move_closer_to_source"):
        return False
    if source_id is None or fear_source_id is None:
        return False
    return source_id == fear_source_id


__all__ = [
    "ability_check_disadvantage",
    "auto_fail_str_dex_saves",
    "can_speak",
    "can_take_actions",
    "can_take_bonus_action",
    "can_take_reaction",
    "cannot_attack",
    "cannot_move_closer_to",
    "crit_within_5ft",
    "speed_zero",
]

