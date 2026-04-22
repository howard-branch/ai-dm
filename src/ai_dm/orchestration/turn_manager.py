from __future__ import annotations

from ai_dm.game.combat_machine import CombatMachine


class TurnManager:
    """Thin coordinator over :class:`CombatMachine`."""

    def __init__(self, combat: CombatMachine | None = None) -> None:
        self.combat = combat

    def next_turn(self) -> None:
        if self.combat is None or self.combat.state is None:
            return
        nxt = self.combat.end_turn()
        if nxt is None:
            self.combat.begin_round()
        self.combat.request_action()
