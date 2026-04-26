"""SRD 5.2 conditions — data-driven catalog.

The catalog lives at ``assets/srd5_2/core/conditions.json`` and is
shared with the Foundry mirror. The legacy ``attacker_mod`` /
``target_mod`` / ``merge_advantage`` API is preserved for backwards
compatibility (call sites: rules engine, combat machine, tests).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ai_dm.rules.srd_core import load

_DATA = load("conditions")
_RECORDS: list[dict] = list(_DATA.get("conditions", []))

# Public, ordered list of all 15 SRD condition keys (incl. exhaustion).
ALL_CONDITIONS: tuple[str, ...] = tuple(rec["key"] for rec in _RECORDS)
CONDITION_LABELS: dict[str, str] = {rec["key"]: rec["label"] for rec in _RECORDS}
_EFFECTS: dict[str, dict] = {rec["key"]: dict(rec.get("effects", {})) for rec in _RECORDS}

# Runtime alias kept so ``from .conditions import Condition`` keeps working.
Condition = str  # type: ignore[assignment]


def effects(condition: str) -> dict:
    """Return a copy of the effects dict for ``condition`` (or ``{}``)."""
    return dict(_EFFECTS.get(condition, {}))


def implied(conditions: Iterable[str]) -> set[str]:
    """Expand ``conditions`` with any conditions implied per SRD.

    * ``unconscious`` ⇒ ``{prone, incapacitated}``
    * ``paralyzed`` / ``stunned`` / ``petrified`` ⇒ ``incapacitated``
    """
    out = set(conditions)
    changed = True
    while changed:
        changed = False
        for c in list(out):
            ef = _EFFECTS.get(c, {})
            for key, target in (
                ("incapacitated_implied", "incapacitated"),
                ("prone_implied", "prone"),
            ):
                if ef.get(key) and target not in out:
                    out.add(target)
                    changed = True
    return out


# --------------------------------------------------------------------- #
# Attack modifiers (back-compat surface used by rules engine)
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class AttackModifier:
    advantage: bool = False
    disadvantage: bool = False
    auto_miss: bool = False
    auto_hit: bool = False


def _has_any(cs: set[str], key: str) -> bool:
    return any(_EFFECTS.get(c, {}).get(key) for c in cs)


def attacker_mod(conditions: Iterable[str]) -> AttackModifier:
    """How an attacker's own conditions affect their attack roll."""
    cs = implied(conditions)
    if not cs:
        return AttackModifier()
    has_adv = _has_any(cs, "attacker_advantage")
    has_dis = _has_any(cs, "attacker_disadvantage")
    return AttackModifier(
        advantage=has_adv and not has_dis,
        disadvantage=has_dis and not has_adv,
    )


def target_mod(conditions: Iterable[str]) -> AttackModifier:
    """How a target's conditions affect attacks against them.

    Prone splits melee / ranged; for the MVP we collapse to "advantage"
    (melee bias), matching the previous behaviour.
    """
    cs = implied(conditions)
    if not cs:
        return AttackModifier()
    has_adv = (
        _has_any(cs, "target_advantage")
        or _has_any(cs, "target_advantage_melee")
    )
    has_dis = _has_any(cs, "target_disadvantage")
    return AttackModifier(
        advantage=has_adv and not has_dis,
        disadvantage=has_dis and not has_adv,
    )


def merge_advantage(*mods: AttackModifier) -> str:
    """Combine modifiers; per SRD any-of-each cancels to ``"normal"``."""
    has_adv = any(m.advantage for m in mods)
    has_dis = any(m.disadvantage for m in mods)
    if has_adv and not has_dis:
        return "advantage"
    if has_dis and not has_adv:
        return "disadvantage"
    return "normal"


def auto_fail_saves(conditions: Iterable[str]) -> set[str]:
    """Set of ability keys whose saves auto-fail under ``conditions``."""
    out: set[str] = set()
    for c in implied(conditions):
        for ab in _EFFECTS.get(c, {}).get("auto_fail_saves", ()) or ():
            out.add(str(ab))
    return out


def speed_zero(conditions: Iterable[str]) -> bool:
    """True if any active condition reduces speed to 0."""
    return _has_any(implied(conditions), "speed_zero")


def crit_on_5ft(conditions: Iterable[str]) -> bool:
    """True if attacks within 5 ft auto-crit (paralyzed/unconscious)."""
    return _has_any(implied(conditions), "attacks_within_5ft_crit")


