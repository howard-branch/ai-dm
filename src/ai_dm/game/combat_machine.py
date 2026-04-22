"""Turn-based combat state machine.

Phases:
    idle -> rolling_initiative -> in_round -> awaiting_action ->
    resolving_action -> in_round (next actor) -> ... -> ended

Every transition publishes a ``combat.phase_changed`` event on the bus
and (optionally) dispatches a Foundry highlight command for the current
actor through the Phase-1 ``CommandRouter``.
"""
from __future__ import annotations

import logging
import random
from typing import Iterable

from ai_dm.ai.schemas import Command as AICommand
from ai_dm.game.combat_state import (
    ActionLogEntry,
    CombatPhase,
    CombatState,
    Participant,
)
from ai_dm.orchestration.event_bus import EventBus

logger = logging.getLogger("ai_dm.combat")


class InvalidCombatTransition(RuntimeError):
    pass


_ALLOWED_TRANSITIONS: dict[CombatPhase, set[CombatPhase]] = {
    "idle": {"rolling_initiative", "ended"},
    "rolling_initiative": {"in_round", "ended"},
    "in_round": {"awaiting_action", "ended"},
    "awaiting_action": {"resolving_action", "ended"},
    "resolving_action": {"in_round", "ended"},
    "ended": set(),
}


class CombatMachine:
    def __init__(
        self,
        *,
        event_bus: EventBus,
        command_router=None,  # ai_dm.orchestration.command_router.CommandRouter
        rng: random.Random | None = None,
        auto_highlight: bool = True,
    ) -> None:
        self.event_bus = event_bus
        self.command_router = command_router
        self.rng = rng or random.Random()
        self.auto_highlight = auto_highlight
        self.state: CombatState | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start_encounter(
        self,
        encounter_id: str,
        participants: Iterable[Participant | dict],
    ) -> CombatState:
        parts = [
            p if isinstance(p, Participant) else Participant.model_validate(p)
            for p in participants
        ]
        self.state = CombatState(
            encounter_id=encounter_id,
            phase="idle",
            participants=parts,
        )
        self._publish("combat.encounter_started", {"encounter_id": encounter_id})
        return self.state

    def roll_initiative(self) -> list[Participant]:
        s = self._require_state()
        self._transition("rolling_initiative")
        for p in s.participants:
            if p.initiative is None:
                p.initiative = self.rng.randint(1, 20)
        s.participants.sort(key=lambda p: (-(p.initiative or 0), p.name))
        s.current_index = 0
        self._publish(
            "combat.initiative_rolled",
            {"order": [p.actor_id for p in s.participants]},
        )
        return list(s.participants)

    def begin_round(self) -> int:
        s = self._require_state()
        self._transition("in_round")
        s.round += 1
        s.current_index = 0
        self._publish("combat.round_started", {"round": s.round})
        return s.round

    def request_action(self) -> Participant:
        s = self._require_state()
        self._transition("awaiting_action")
        actor = s.participants[s.current_index]
        self._publish(
            "combat.actor_highlighted",
            {"actor_id": actor.actor_id, "round": s.round},
        )
        if self.auto_highlight and self.command_router is not None and actor.token_id:
            try:
                self.command_router.dispatch([
                    AICommand(type="highlight_object", target_id=actor.token_id)
                ])
            except Exception as exc:  # noqa: BLE001
                logger.warning("auto-highlight failed: %s", exc)
        return actor

    def submit_action(
        self,
        kind: str,
        payload: dict | None = None,
        result: dict | None = None,
    ) -> ActionLogEntry:
        s = self._require_state()
        self._transition("resolving_action")
        actor = s.participants[s.current_index]
        entry = ActionLogEntry(
            round=s.round,
            actor_id=actor.actor_id,
            kind=kind,
            payload=payload or {},
            result=result or {},
        )
        s.log.append(entry)
        self._publish(
            "combat.action_resolved",
            {
                "actor_id": actor.actor_id,
                "kind": kind,
                "round": s.round,
            },
        )
        return entry

    def end_turn(self) -> Participant | None:
        s = self._require_state()
        self._transition("in_round")
        s.current_index += 1
        if s.current_index >= len(s.participants):
            return None  # round complete
        return s.participants[s.current_index]

    def end_encounter(self, reason: str = "ended") -> CombatState:
        s = self._require_state()
        self._transition("ended")
        self._publish(
            "combat.encounter_ended",
            {"encounter_id": s.encounter_id, "reason": reason},
        )
        return s

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict | None:
        return self.state.model_dump() if self.state is not None else None

    def restore(self, snapshot: dict | None) -> None:
        self.state = CombatState.model_validate(snapshot) if snapshot else None

    # ------------------------------------------------------------------ #

    def _require_state(self) -> CombatState:
        if self.state is None:
            raise InvalidCombatTransition("no encounter in progress")
        return self.state

    def _transition(self, target: CombatPhase) -> None:
        s = self._require_state()
        allowed = _ALLOWED_TRANSITIONS.get(s.phase, set())
        if target not in allowed and target != s.phase:
            raise InvalidCombatTransition(
                f"cannot transition from {s.phase!r} to {target!r}"
            )
        if target != s.phase:
            previous = s.phase
            s.phase = target
            self._publish(
                "combat.phase_changed",
                {"from": previous, "to": target, "round": s.round},
            )

    def _publish(self, event: str, payload: dict) -> None:
        try:
            self.event_bus.publish(event, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("event publish failed for %s: %s", event, exc)

