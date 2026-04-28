"""Thin facade around :class:`ai_dm.game.combatant_state.Inventory`.

Kept for callers that previously injected an ``InventoryManager``.
The canonical state lives on each combatant's
:attr:`CombatantState.inventory`; this class just narrows the API.
"""
from __future__ import annotations

from typing import Any

from ai_dm.game.combatant_state import CombatantState, Inventory


class InventoryManager:
    def __init__(self, combatant: CombatantState | None = None,
                 inventory: Inventory | None = None) -> None:
        if combatant is not None:
            self._inv = combatant.inventory
        elif inventory is not None:
            self._inv = inventory
        else:
            self._inv = Inventory()

    @property
    def inventory(self) -> Inventory:
        return self._inv

    def list_items(self) -> list[dict[str, Any]]:
        return [it.model_dump() for it in self._inv.items]

    def give(self, item_key: str, qty: int = 1) -> dict[str, Any]:
        return self._inv.give(item_key, qty).model_dump()

    def drop(self, instance_id: str, qty: int | None = None) -> dict[str, Any] | None:
        out = self._inv.drop(instance_id, qty)
        return out.model_dump() if out is not None else None

    def equip(self, instance_id: str, slot: str, *, two_handed: bool = False) -> None:
        self._inv.equip(instance_id, slot, two_handed=two_handed)  # type: ignore[arg-type]

    def unequip(self, slot: str) -> str | None:
        return self._inv.unequip(slot)  # type: ignore[arg-type]

    def attune(self, instance_id: str) -> bool:
        return self._inv.attune(instance_id)

    def end_attunement(self, instance_id: str) -> bool:
        return self._inv.end_attunement(instance_id)


__all__ = ["InventoryManager"]
