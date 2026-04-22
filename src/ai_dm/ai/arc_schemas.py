"""Schemas for the long-form story planner."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ThreadStatus = Literal["open", "active", "resolved", "abandoned"]


class Beat(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    summary: str = ""
    triggers: list[str] = Field(default_factory=list)
    completes_on: dict | None = None  # event-name + payload predicate
    rewards: list[str] = Field(default_factory=list)


class Scene(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str = ""
    summary: str = ""
    beats: list[Beat] = Field(default_factory=list)


class Chapter(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    title: str = ""
    summary: str = ""
    scenes: list[Scene] = Field(default_factory=list)
    encounters: list[dict] = Field(default_factory=list)
    npcs: list[dict] = Field(default_factory=list)


class StoryThread(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    summary: str = ""
    status: ThreadStatus = "open"


class ArcState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_chapter: str | None = None
    current_scene: str | None = None
    beats_completed: list[str] = Field(default_factory=list)
    threads: list[StoryThread] = Field(default_factory=list)

