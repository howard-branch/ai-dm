from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION: Literal["2.0"] = "2.0"


class DiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    actor: str | None = None
    skill: str | None = None
    dc: int | None = None
    reason: str | None = None


class Command(BaseModel):
    """Loose AI-side command shape.

    Strict typing happens later in the validator/router. We allow
    ``target`` (anchor name) so the location service can resolve to (x, y).
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    target_id: str | None = None
    token_id: str | None = None
    actor_id: str | None = None
    scene_id: str | None = None
    target: str | None = None  # anchor or zone name
    x: int | None = None
    y: int | None = None
    # Movement extensions:
    #   distance_ft   — partial move ("30 ft toward altar")
    #   direction     — "toward" / "away" / cardinal ("north", "ne")
    #   formation_index / formation_count — slot in a multi-actor
    #     party move so members fan out into a ring instead of
    #     stacking on the destination tile.
    distance_ft: int | None = None
    direction: str | None = None
    formation_index: int | None = None
    formation_count: int | None = None
    patch: dict = Field(default_factory=dict)


class StateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    key: str | None = None
    value: object | None = None


class NPCDialogueLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    npc_id: str
    text: str
    tone: str | None = None
    target: str | None = None  # who is being addressed


class AIOutput(BaseModel):
    """Structured DM output produced by the LLM.

    Forbids extra keys so the LLM can't smuggle prose into unknown fields.
    Use :func:`ai_dm.ai.response_parser.safe_parse_ai_output` to handle
    malformed payloads gracefully.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"] = SCHEMA_VERSION
    narration: str
    spoken_dialogue: str = ""
    dialogue: list[NPCDialogueLine] = Field(default_factory=list)
    dice_requests: list[DiceRequest] = Field(default_factory=list)
    commands: list[Command] = Field(default_factory=list)
    state_updates: list[StateUpdate] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

