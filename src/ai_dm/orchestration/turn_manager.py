from __future__ import annotations

import logging

from ai_dm.game.combat_machine import CombatMachine

logger = logging.getLogger("ai_dm.orchestration.turn_manager")


class TurnManager:
    """Thin coordinator over :class:`CombatMachine`."""

    def __init__(self, combat: CombatMachine | None = None) -> None:
        self.combat = combat

    def next_turn(self) -> None:
        if self.combat is None or self.combat.state is None:
            logger.info(
                "npc_turn: turn_manager.next_turn() noop — combat=%s state=%s",
                self.combat,
                getattr(self.combat, "state", None) if self.combat else None,
            )
            return
        s = self.combat.state
        logger.info(
            "npc_turn: turn_manager.next_turn() phase=%s round=%s idx=%s/%s",
            s.phase, s.round, s.current_index, len(s.participants),
        )
        try:
            nxt = self.combat.end_turn()
            logger.info(
                "npc_turn: end_turn → next=%s (None means round complete)",
                getattr(nxt, "actor_id", None),
            )
            if nxt is None:
                self.combat.begin_round()
                logger.info(
                    "npc_turn: begin_round → round=%s", self.combat.state.round,
                )
            self.combat.request_action()
        except Exception:
            logger.exception("npc_turn: turn_manager.next_turn() failed")
            raise

    def kickoff(self) -> None:
        """Roll initiative, open round 1, and highlight the first actor.

        Used right after :meth:`CombatMachine.start_encounter` so the
        spotlight actually lands on someone — otherwise the encounter
        sits in ``idle`` forever and the NPC turn driver, which only
        wakes on ``combat.actor_highlighted``, never fires.
        """
        if self.combat is None or self.combat.state is None:
            logger.info("npc_turn: kickoff noop — no combat/state")
            return
        logger.info(
            "npc_turn: kickoff phase=%s participants=%d",
            self.combat.state.phase, len(self.combat.state.participants),
        )
        try:
            if self.combat.state.phase == "idle":
                self.combat.roll_initiative()
                logger.info(
                    "npc_turn: kickoff rolled initiative → order=%s",
                    [p.actor_id for p in self.combat.state.participants],
                )
            if self.combat.state.phase == "rolling_initiative":
                self.combat.begin_round()
            if self.combat.state.phase == "in_round":
                self.combat.request_action()
        except Exception:
            logger.exception("npc_turn: kickoff failed")
            raise

