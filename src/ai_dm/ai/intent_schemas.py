"""Schemas for player-facing intents.

Intents are produced by the :class:`IntentParser` from raw player text
(or speech transcript). They are the structured input that the
:class:`ActionResolver`, :class:`CommandRouter`, and narrator all consume.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IntentType = Literal[
    "move",
    "travel",   # cross-scene travel (different from in-scene `move`)
    "attack",
    "cast_spell",
    "skill_check",
    "interact",
    "speak",
    "use_item",
    # --- combat action menu (5e SRD) ----------------------------- #
    "dash",
    "disengage",
    "dodge",
    "help",
    "hide",
    "ready",
    "end_turn",
    # ------------------------------------------------------------ #
    "query_world",
    "meta",
    "unknown",
]


class PlayerIntent(BaseModel):
    """Structured representation of a player utterance."""

    model_config = ConfigDict(extra="forbid")

    type: IntentType = "unknown"
    verb: str | None = None
    actor_id: str | None = None
    target_id: str | None = None
    target_anchor: str | None = None  # named map anchor (e.g. "altar")
    weapon: str | None = None
    skill: str | None = None
    spell: str | None = None
    dc: int | None = None
    modifier: int | None = None
    quoted_speech: str | None = None
    raw_text: str = ""
    confidence: float = 0.0  # 0..1
    notes: str | None = None
    extra: dict = Field(default_factory=dict)

