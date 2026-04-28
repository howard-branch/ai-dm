"""Initiative — SRD 5.2.

Single source of truth: ``assets/srd5_2/core/initiative.json``.

Initiative is a dexterity check (1d20 + DEX modifier + any feature
bonus). Higher result acts first; ties are broken by raw DEX modifier
then a deterministic random pick.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ai_dm.rules.srd_core import load

_DATA = load("initiative")
ABILITY: str = str(_DATA["ability"])
TIE_BREAK: str = str(_DATA["tie_break"])
SURPRISE_SKIPS_FIRST_TURN: bool = bool(_DATA["surprise"]["skip_first_turn"])


@dataclass
class InitiativeRoll:
    actor_id: str
    roll: int
    modifier: int
    total: int
    dex_mod: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "roll": self.roll,
            "modifier": self.modifier,
            "total": self.total,
            "dex_mod": self.dex_mod,
        }


def roll_initiative(
    actor_id: str,
    *,
    roller: Any,
    modifier: int = 0,
    dex_mod: int | None = None,
    advantage: str = "normal",
) -> InitiativeRoll:
    """Roll 1d20 + ``modifier`` for ``actor_id``."""
    rr = roller.roll("1d20", advantage=advantage)
    nat = int(rr.kept[0])
    return InitiativeRoll(
        actor_id=actor_id,
        roll=nat,
        modifier=int(modifier),
        total=nat + int(modifier),
        dex_mod=int(dex_mod if dex_mod is not None else modifier),
    )


def sort_order(rolls: Iterable[InitiativeRoll], *, rng: Any | None = None) -> list[str]:
    """Return actor_ids sorted by initiative (high → low) with SRD tie break."""
    items = list(rolls)
    # Stable sort: total desc, then dex_mod desc; remaining ties resolved
    # by deterministic random pick if rng provided, else by actor_id.
    items.sort(key=lambda r: (-r.total, -r.dex_mod, r.actor_id))
    if rng is not None:
        # Re-shuffle within still-tied (total, dex_mod) groups.
        out: list[InitiativeRoll] = []
        i = 0
        while i < len(items):
            j = i
            while (
                j + 1 < len(items)
                and items[j + 1].total == items[i].total
                and items[j + 1].dex_mod == items[i].dex_mod
            ):
                j += 1
            group = items[i : j + 1]
            if len(group) > 1:
                rng.shuffle(group)
            out.extend(group)
            i = j + 1
        items = out
    return [r.actor_id for r in items]


__all__ = [
    "ABILITY",
    "InitiativeRoll",
    "SURPRISE_SKIPS_FIRST_TURN",
    "TIE_BREAK",
    "roll_initiative",
    "sort_order",
]

