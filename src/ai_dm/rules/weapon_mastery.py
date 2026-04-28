"""SRD 5.2 / 2024 weapon mastery properties.

Single source of truth: ``assets/srd5_2/core/weapon_mastery.json``.

The 8 mastery properties are: cleave, graze, nick, push, sap, slow,
topple, vex. Each weapon in :mod:`ai_dm.rules.weapons` is tagged with
at most one mastery slug. Classes that get the Weapon Mastery feature
(barbarian, fighter, monk, paladin, ranger, rogue) can use up to N
masteries simultaneously per :func:`mastery_count_for`.

This module is rules data + dispatch; randomness (the Topple Con save,
the d20 the Cleave bonus damage uses) is intentionally outside its
remit and computed by the action resolver.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ai_dm.rules.srd_core import load

_DATA = load("weapon_mastery")

MasteryKey = Literal["cleave", "graze", "nick", "push", "sap", "slow", "topple", "vex"]
MASTERY_KEYS: frozenset[MasteryKey] = frozenset(  # type: ignore[assignment]
    m["key"] for m in _DATA["masteries"]
)


@dataclass(frozen=True)
class Mastery:
    key: str
    label: str
    trigger: str
    effect: str


_BY_KEY: dict[str, Mastery] = {
    m["key"]: Mastery(key=m["key"], label=m["label"], trigger=m["trigger"], effect=m["effect"])
    for m in _DATA["masteries"]
}


def get_mastery(key: str) -> Mastery | None:
    return _BY_KEY.get(key)


def all_masteries() -> list[Mastery]:
    return list(_BY_KEY.values())


def mastery_count_for(class_key: str, level: int) -> int:
    """How many weapon masteries the given class can have active at ``level``.

    Returns 0 for classes that don't get the Weapon Mastery feature.
    """
    table = (_DATA.get("class_progression") or {}).get(class_key.lower())
    if not table:
        return 0
    best = 0
    for lvl_str, n in table.items():
        try:
            lvl = int(lvl_str)
        except (TypeError, ValueError):
            continue
        if lvl <= int(level):
            best = max(best, int(n))
    return best


def apply_mastery(
    name: str | None,
    *,
    attacker: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    weapon: dict[str, Any] | None = None,
    hit: bool,
    damage: int = 0,
    proficiency_bonus: int = 0,
    attack_ability_mod: int = 0,
) -> dict[str, Any]:
    """Compute the effect of a mastery for one attack.

    Returns a structured dict the action resolver can apply. Unknown or
    null mastery → ``{"key": None, "effects": []}``. Effects are
    deterministic descriptions; saving throws / additional rolls are
    *requested* (not rolled) so the resolver remains the only place
    that touches the dice bag.
    """
    if not name:
        return {"key": None, "effects": []}
    name = name.lower()
    if name not in MASTERY_KEYS:
        return {"key": None, "effects": []}

    out: dict[str, Any] = {"key": name, "effects": []}
    target_id = (target or {}).get("actor_id")

    if name == "cleave" and hit and weapon and "heavy" in (weapon.get("properties") or []):
        die = (weapon.get("damage") or {}).get("dice")
        out["effects"].append({"kind": "cleave", "weapon_die": die,
                               "damage_type": (weapon.get("damage") or {}).get("type")})
    elif name == "graze" and not hit:
        bonus = max(0, int(attack_ability_mod))
        if bonus:
            out["effects"].append({"kind": "graze_damage", "amount": bonus, "target_id": target_id,
                                   "damage_type": (weapon.get("damage") or {}).get("type") if weapon else None})
    elif name == "nick":
        out["effects"].append({"kind": "nick_extra_attack_in_action"})
    elif name == "push" and hit:
        out["effects"].append({"kind": "push", "target_id": target_id, "distance_ft": 10, "max_size": "large"})
    elif name == "sap" and hit:
        out["effects"].append({"kind": "disadvantage_on_next_attack", "target_id": target_id})
    elif name == "slow" and hit and damage > 0:
        out["effects"].append({"kind": "speed_reduction", "target_id": target_id, "amount_ft": -10})
    elif name == "topple" and hit:
        dc = 8 + int(attack_ability_mod) + int(proficiency_bonus)
        out["effects"].append({"kind": "request_save", "target_id": target_id,
                               "ability": "con", "dc": dc, "on_fail": "prone"})
    elif name == "vex" and hit and damage > 0:
        out["effects"].append({"kind": "advantage_on_next_attack", "target_id": target_id})
    return out


__all__ = [
    "MASTERY_KEYS",
    "Mastery",
    "MasteryKey",
    "all_masteries",
    "apply_mastery",
    "get_mastery",
    "mastery_count_for",
]

