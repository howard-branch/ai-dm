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
from ai_dm.rules import initiative as _initiative
from ai_dm.rules import turn as _turn
from ai_dm.rules.dice import DiceRoller

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
        roller = DiceRoller(rng=self.rng)
        rolls: list[_initiative.InitiativeRoll] = []
        for p in s.participants:
            mod = int(getattr(p, "initiative_bonus", 0) or 0)
            if p.initiative is None:
                r = _initiative.roll_initiative(
                    p.actor_id, roller=roller, modifier=mod, dex_mod=mod,
                )
                p.initiative = r.total
                rolls.append(r)
            else:
                rolls.append(_initiative.InitiativeRoll(
                    actor_id=p.actor_id, roll=p.initiative - mod,
                    modifier=mod, total=p.initiative, dex_mod=mod,
                ))
        order = _initiative.sort_order(rolls, rng=self.rng)
        rank = {aid: i for i, aid in enumerate(order)}
        s.participants.sort(key=lambda p: rank.get(p.actor_id, 1_000_000))
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
        # Reaction + per-round resources refresh for every combatant
        # before the first actor of the round starts their turn.
        for p in s.participants:
            p.start_of_round()
        self._publish("combat.round_started", {"round": s.round})
        return s.round

    def request_action(self) -> Participant:
        s = self._require_state()
        self._transition("awaiting_action")
        actor = s.participants[s.current_index]
        # Action economy + per-turn resources reset on the active actor.
        actor.start_of_turn()
        logger.info(
            "npc_turn: request_action → highlighting actor=%s name=%s "
            "controller=%s team=%s hp=%s/%s round=%s idx=%s/%s",
            actor.actor_id, getattr(actor, "name", None),
            getattr(actor, "controller", None), getattr(actor, "team", None),
            getattr(actor, "hp", None), getattr(actor, "max_hp", None),
            s.round, s.current_index, len(s.participants),
        )
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
        # Fire SRD end-of-turn hook on the actor whose turn just ended,
        # before we advance the index. Publishes a `combat.turn_ended`
        # event so subscribers (concentration ticks, condition timers)
        # can react.
        if 0 <= s.current_index < len(s.participants):
            actor = s.participants[s.current_index]
            report = _turn.end_of_turn(actor)
            self._publish("combat.turn_ended", {
                "actor_id": actor.actor_id, "round": s.round, **report,
            })
        # Some callers (notably the PC-attack resolver) advance the
        # turn without ever calling ``submit_action`` — e.g. when a
        # player resolves an attack via chat and we just want to pass
        # the spotlight. In that case we're still in
        # ``awaiting_action`` and the direct hop to ``in_round`` is
        # illegal; hop through ``resolving_action`` first so the
        # state-machine invariants hold.
        if s.phase == "awaiting_action":
            self._transition("resolving_action")
        self._transition("in_round")
        s.current_index += 1
        if s.current_index >= len(s.participants):
            logger.info(
                "npc_turn: end_turn → end of round %s (idx wrapped past %d)",
                s.round, len(s.participants),
            )
            return None  # round complete
        nxt = s.participants[s.current_index]
        logger.info(
            "npc_turn: end_turn → idx=%d/%d next=%s controller=%s",
            s.current_index, len(s.participants),
            nxt.actor_id, getattr(nxt, "controller", None),
        )
        return nxt

    def end_encounter(self, reason: str = "ended") -> CombatState:
        s = self._require_state()
        self._transition("ended")
        for p in s.participants:
            p.end_encounter()
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
            logger.debug(
                "npc_turn: combat phase %s → %s round=%s idx=%s",
                previous, target, s.round, s.current_index,
            )
            self._publish(
                "combat.phase_changed",
                {"from": previous, "to": target, "round": s.round},
            )

    def _publish(self, event: str, payload: dict) -> None:
        try:
            self.event_bus.publish(event, payload)
        except Exception as exc:  # noqa: BLE001
            logger.exception("event publish failed for %s: %s", event, exc)

