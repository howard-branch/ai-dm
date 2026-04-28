"""Concentration — SRD 5.2.

Single source of truth: ``assets/srd5_2/core/concentration.json``.

When a concentrating creature takes damage it must make a Constitution
save (DC = max(10, floor(damage / 2))). Concentration also drops on
``incapacitated`` / death / starting a second concentration spell, and
when the creature falls to 0 HP.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_dm.rules.srd_core import load

_DATA = load("concentration")
SAVE_ABILITY: str = str(_DATA["save"])
MIN_DC: int = int(_DATA["min_dc"])
BROKEN_BY: tuple[str, ...] = tuple(_DATA["broken_by"])
AUTO_DROP_AT_ZERO_HP: bool = bool(_DATA["auto_drop_at_zero_hp"])


def dc_for_damage(amount: int) -> int:
    """SRD: max(10, floor(damage / 2))."""
    return max(MIN_DC, int(amount) // 2)


@dataclass
class ConcentrationSaveResult:
    success: bool
    roll: int
    total: int
    dc: int
    broken: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "roll": self.roll,
            "total": self.total,
            "dc": self.dc,
            "broken": self.broken,
        }


def roll_save(
    actor: Any,
    *,
    damage: int,
    roller: Any,
    save_modifier: int | None = None,
) -> ConcentrationSaveResult:
    """Roll a Con save against ``damage``. Drops concentration on failure."""
    dc = dc_for_damage(damage)
    if save_modifier is None:
        saves = getattr(actor, "saving_throws", None) or {}
        save_modifier = int(saves.get(SAVE_ABILITY, 0))
    rr = roller.roll("1d20")
    nat = int(rr.kept[0])
    total = nat + int(save_modifier)
    success = total >= dc
    broken = False
    if not success:
        broken = break_(actor, reason="failed_save")
    return ConcentrationSaveResult(success, nat, total, dc, broken)


def start(
    actor: Any,
    spell_id: str,
    *,
    target_ids: list[str] | None = None,
    save_dc: int | None = None,
    started_round: int | None = None,
    name: str | None = None,
) -> bool:
    """Begin concentrating on ``spell_id`` (replacing any prior spell)."""
    try:
        from ai_dm.game.combatant_state import Concentration
    except Exception:  # noqa: BLE001
        return False
    if not hasattr(actor, "concentration"):
        return False
    actor.concentration = Concentration(
        spell_id=str(spell_id),
        name=name or str(spell_id),
        target_ids=list(target_ids or []),
        save_dc=save_dc,
        started_round=started_round,
    )
    return True


def break_(actor: Any, *, reason: str = "manual") -> bool:
    """Drop ``actor``'s concentration. Returns True if anything was broken."""
    if getattr(actor, "concentration", None) is None:
        return False
    actor.concentration = None
    return True


def on_damage(
    actor: Any,
    amount: int,
    *,
    roller: Any,
) -> ConcentrationSaveResult | None:
    """Convenience: roll the save iff ``actor`` is concentrating."""
    if getattr(actor, "concentration", None) is None or amount <= 0:
        return None
    return roll_save(actor, damage=amount, roller=roller)


def on_condition(actor: Any, condition: str) -> bool:
    """If ``condition`` is in the SRD ``broken_by`` set, drop concentration."""
    if condition in BROKEN_BY or condition in {
        "incapacitated", "unconscious", "stunned", "paralyzed", "petrified",
    }:
        return break_(actor, reason=f"condition:{condition}")
    return False


__all__ = [
    "AUTO_DROP_AT_ZERO_HP",
    "BROKEN_BY",
    "ConcentrationSaveResult",
    "MIN_DC",
    "SAVE_ABILITY",
    "break_",
    "dc_for_damage",
    "on_condition",
    "on_damage",
    "roll_save",
    "start",
]

