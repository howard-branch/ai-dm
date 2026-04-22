from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field


class BaseCommand(BaseModel):
    type: str


class MoveTokenCommand(BaseCommand):
    type: Literal["move_token"] = "move_token"
    token_id: str
    x: int
    y: int
    scene_id: str | None = None


class ActivateSceneCommand(BaseCommand):
    type: Literal["activate_scene"] = "activate_scene"
    scene_id: str


class UpdateActorCommand(BaseCommand):
    type: Literal["update_actor"] = "update_actor"
    actor_id: str
    patch: dict = Field(default_factory=dict)


class HighlightObjectCommand(BaseCommand):
    type: Literal["highlight_object"] = "highlight_object"
    target_id: str


class CreateSceneCommand(BaseCommand):
    type: Literal["create_scene"] = "create_scene"
    name: str
    width: int = 4000
    height: int = 3000
    grid: int = 100


class SpawnTokenCommand(BaseCommand):
    type: Literal["spawn_token"] = "spawn_token"
    scene_id: str
    actor_id: str
    x: int
    y: int
    name: str | None = None


class CreateActorCommand(BaseCommand):
    type: Literal["create_actor"] = "create_actor"
    name: str
    actor_type: str = "npc"
    # Optional dnd5e (or other system) data merged into the new actor
    # via Actor.create / actor.update. Use dotted keys (e.g.
    # "system.attributes.hp.max") to target nested fields.
    system: dict = Field(default_factory=dict)
    img: str | None = None


# ------------------------------------------------------------------ #
# Inverse / read-back commands used by the BatchExecutor for rollback.
# ------------------------------------------------------------------ #

class DeleteTokenCommand(BaseCommand):
    type: Literal["delete_token"] = "delete_token"
    scene_id: str
    token_id: str


class DeleteActorCommand(BaseCommand):
    type: Literal["delete_actor"] = "delete_actor"
    actor_id: str


class DeleteSceneCommand(BaseCommand):
    type: Literal["delete_scene"] = "delete_scene"
    scene_id: str


class ReadTokenCommand(BaseCommand):
    type: Literal["read_token"] = "read_token"
    scene_id: str
    token_id: str


class ReadActorCommand(BaseCommand):
    type: Literal["read_actor"] = "read_actor"
    actor_id: str


class ReadActiveSceneCommand(BaseCommand):
    type: Literal["read_active_scene"] = "read_active_scene"


# ------------------------------------------------------------------ #
# Phase 3: journal entries.
# ------------------------------------------------------------------ #

class CreateJournalCommand(BaseCommand):
    type: Literal["create_journal"] = "create_journal"
    title: str
    content: str = ""
    folder: str | None = None


class UpdateJournalCommand(BaseCommand):
    type: Literal["update_journal"] = "update_journal"
    journal_id: str
    title: str | None = None
    content: str | None = None


GameCommand = Union[
    MoveTokenCommand,
    ActivateSceneCommand,
    UpdateActorCommand,
    HighlightObjectCommand,
    CreateSceneCommand,
    SpawnTokenCommand,
    CreateActorCommand,
    DeleteTokenCommand,
    DeleteActorCommand,
    DeleteSceneCommand,
    ReadTokenCommand,
    ReadActorCommand,
    ReadActiveSceneCommand,
    CreateJournalCommand,
    UpdateJournalCommand,
]