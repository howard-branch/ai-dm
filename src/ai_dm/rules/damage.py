"""Damage, healing, resistance / vulnerability / immunity, and temp HP.

The damage-type catalog comes from
``assets/srd5_2/core/damage_types.json`` so the Foundry mirror enforces
the same set of types.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ai_dm.rules.srd_core import load

_DATA = load("damage_types")
DAMAGE_TYPES: tuple[str, ...] = tuple(_DATA["types"])
PHYSICAL_DAMAGE_TYPES: frozenset[str] = frozenset(_DATA["physical"])
UNTYPED_ALIAS: str = _DATA.get("untyped_alias", "untyped")


def is_known_damage_type(name: str) -> bool:
    return name in DAMAGE_TYPES


def apply_modifiers(
    amount: int,
    damage_type: str,
    *,
    resistances: Iterable[str] | None = None,
    vulnerabilities: Iterable[str] | None = None,
    immunities: Iterable[str] | None = None,
) -> int:
    """Apply resistance / vulnerability / immunity to a raw damage amount.

    SRD ordering:
      * immunity → 0
      * if both resistant and vulnerable → cancel (full damage)
      * vulnerability → ``amount * 2``
      * resistance → ``amount // 2`` (round down)
    """
    if amount <= 0:
        return 0
    imm = set(immunities or ())
    if damage_type in imm:
        return 0
    res = set(resistances or ())
    vuln = set(vulnerabilities or ())
    in_res = damage_type in res
    in_vuln = damage_type in vuln
    if in_res and in_vuln:
        return amount
    if in_vuln:
        return amount * 2
    if in_res:
        return amount // 2
    return amount


# --------------------------------------------------------------------- #
# Target-aware helpers (work with any object exposing the named attrs)
# --------------------------------------------------------------------- #


@dataclass
class DamageOutcome:
    requested: int
    absorbed_by_temp_hp: int
    dealt: int
    hp_before: int
    hp_after: int
    dropped_to_zero: bool
    damage_type: str = "untyped"

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "absorbed_by_temp_hp": self.absorbed_by_temp_hp,
            "dealt": self.dealt,
            "hp_before": self.hp_before,
            "hp_after": self.hp_after,
            "dropped_to_zero": self.dropped_to_zero,
            "damage_type": self.damage_type,
        }


def _get(obj: Any, attr: str, default: Any) -> Any:
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _set(obj: Any, attr: str, value: Any) -> None:
    if isinstance(obj, dict):
        obj[attr] = value
    else:
        setattr(obj, attr, value)


def apply_damage(target: Any, amount: int, damage_type: str = "untyped") -> DamageOutcome:
    """Apply pre-modified ``amount`` damage to ``target``.

    Resistance / vulnerability / immunity should already be applied via
    :func:`apply_modifiers` before calling this — keeping the two phases
    separate makes UI surfacing easier.

    Soaks ``temp_hp`` first (per SRD) then reduces ``hp``. Returns a
    :class:`DamageOutcome` describing what happened.
    """
    requested = max(0, int(amount))
    hp_before = int(_get(target, "hp", 0))
    if requested == 0:
        return DamageOutcome(0, 0, 0, hp_before, hp_before, False, damage_type)

    temp = int(_get(target, "temp_hp", 0))
    absorbed = min(temp, requested)
    if absorbed:
        _set(target, "temp_hp", temp - absorbed)
    remaining = requested - absorbed
    hp_after = max(0, hp_before - remaining)
    _set(target, "hp", hp_after)
    return DamageOutcome(
        requested=requested,
        absorbed_by_temp_hp=absorbed,
        dealt=remaining,
        hp_before=hp_before,
        hp_after=hp_after,
        dropped_to_zero=hp_before > 0 and hp_after == 0,
        damage_type=damage_type,
    )


def apply_healing(target: Any, amount: int) -> int:
    """Heal ``target`` up to ``max_hp`` and return the new HP.

    Healing of 0 or negative does nothing. Per SRD, healing a creature
    that is at 0 HP brings it to ``min(max_hp, amount)`` (≥ 1) and ends
    the unconscious / dying state — callers should reset the death-save
    track separately (see :mod:`ai_dm.rules.death_saves`).
    """
    if amount <= 0:
        return int(_get(target, "hp", 0))
    hp = int(_get(target, "hp", 0))
    max_hp = int(_get(target, "max_hp", hp + amount))
    new_hp = min(max_hp or (hp + amount), hp + int(amount))
    _set(target, "hp", new_hp)
    return new_hp


def grant_temp_hp(target: Any, amount: int) -> int:
    """Temp HP do not stack — take the higher of current and incoming."""
    if amount <= 0:
        return int(_get(target, "temp_hp", 0))
    cur = int(_get(target, "temp_hp", 0))
    new = max(cur, int(amount))
    _set(target, "temp_hp", new)
    return new


__all__ = [
    "DAMAGE_TYPES",
    "DamageOutcome",
    "PHYSICAL_DAMAGE_TYPES",
    "UNTYPED_ALIAS",
    "apply_damage",
    "apply_healing",
    "apply_modifiers",
    "grant_temp_hp",
    "is_known_damage_type",
]

