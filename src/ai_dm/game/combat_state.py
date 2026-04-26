"""Combat state model — pure data.

``Participant`` is now an alias for :class:`ai_dm.game.combatant_state.CombatantState`,
the canonical Python-side combatant record. The old four-field shape
(``actor_id``, ``hp``, ``max_hp``, ``conditions`` …) is preserved as a
strict subset of the new model, so existing constructors keep working
while gaining the full action-economy / resource / spell-slot surface.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_dm.game.combatant_state import CombatantState, Team
from ai_dm.utils.time import now_iso

CombatPhase = Literal[
    "idle",
    "rolling_initiative",
    "in_round",
    "awaiting_action",
    "resolving_action",
    "ended",
]

# Back-compat alias. New code should import ``CombatantState`` directly.
Participant = CombatantState


class ActionLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round: int
    actor_id: str
    kind: str
    payload: dict = Field(default_factory=dict)
    result: dict = Field(default_factory=dict)
    ts: str = Field(default_factory=now_iso)


class CombatState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    encounter_id: str
    phase: CombatPhase = "idle"
    round: int = 0
    current_index: int = 0
    participants: list[CombatantState] = Field(default_factory=list)
    log: list[ActionLogEntry] = Field(default_factory=list)


__all__ = [
    "ActionLogEntry",
    "CombatPhase",
    "CombatState",
    "CombatantState",
    "Participant",
    "Team",
]
