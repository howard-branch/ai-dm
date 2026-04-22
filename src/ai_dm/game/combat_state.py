"""Combat state model — pure data."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_dm.utils.time import now_iso

CombatPhase = Literal[
    "idle",
    "rolling_initiative",
    "in_round",
    "awaiting_action",
    "resolving_action",
    "ended",
]

Team = Literal["party", "foe", "neutral"]


class Participant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str
    token_id: str | None = None
    name: str
    initiative: int | None = None
    hp: int = 0
    max_hp: int = 0
    team: Team = "neutral"
    conditions: list[str] = Field(default_factory=list)


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
    participants: list[Participant] = Field(default_factory=list)
    log: list[ActionLogEntry] = Field(default_factory=list)

