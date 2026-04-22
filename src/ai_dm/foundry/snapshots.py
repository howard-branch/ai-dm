"""Typed snapshots returned by inbound Foundry reads.

Kept deliberately permissive (``extra='allow'``) — Foundry document
shapes evolve and we only care about a few fields.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TokenSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str | None = None
    actor_id: str | None = Field(default=None, alias="actorId")
    scene_id: str | None = Field(default=None, alias="sceneId")
    x: int | None = None
    y: int | None = None
    hp: int | None = None
    max_hp: int | None = Field(default=None, alias="maxHp")


class ActorSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str | None = None
    type: str | None = None
    hp: int | None = None
    max_hp: int | None = Field(default=None, alias="maxHp")
    ac: int | None = None
    img: str | None = None
    system: dict[str, Any] = Field(default_factory=dict)


class SceneSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str | None = None
    width: int | None = None
    height: int | None = None
    grid: int | None = None
    tokens: list[TokenSnapshot] = Field(default_factory=list)

