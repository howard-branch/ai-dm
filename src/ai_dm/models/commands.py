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


class MoveActorToCommand(BaseCommand):
    """Move the token controlled by an actor toward a named target.

    Actor → token resolution and target → coordinate resolution both
    happen on the Foundry side (see token_commands.js#moveActorTo), so
    Python doesn't need a populated anchor registry to route a player's
    "I move toward the altar" intent through to the canvas.

    Provide ``target`` (a name like "altar" / "Bran" / id) **or**
    ``target_id`` (an exact token id) **or** explicit ``x``/``y``.
    """

    type: Literal["move_actor_to"] = "move_actor_to"
    actor_id: str
    target: str | None = None
    target_id: str | None = None
    scene_id: str | None = None
    x: int | None = None
    y: int | None = None
    # Partial / directional move support. ``distance_ft`` caps how
    # far the token travels along the direction implied by ``target``
    # (``toward``/``away``) or by ``direction`` (cardinal). ``direction``
    # is also accepted standalone (no target) for "advance 15 ft north".
    distance_ft: int | None = None
    direction: str | None = None
    # Formation slot for multi-actor party moves. The Foundry-side
    # mover applies offsets in *grid cells* around the resolved
    # destination so members occupy distinct tiles instead of
    # stacking on the target. ``formation_index`` is 0-based; the
    # leader is index 0 and lands on the centre.
    formation_index: int | None = None
    formation_count: int | None = None


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
    # Optional list of Foundry Item documents (weapons, armour, spells,
    # feats) to embed under the actor immediately after creation. Each
    # entry is a dict shaped like {name, type, system: {...}}.
    items: list[dict] = Field(default_factory=list)
    # Extra registry aliases to attach to the created/resolved actor on
    # the Python side (e.g. the canonical pack id "pc_human"). The JS
    # bridge ignores this field; it exists so downstream commands like
    # ``move_actor_to(actor_id="pc_human")`` can be resolved by the
    # validator instead of being passed through verbatim and failing
    # in Foundry with ``no token for actor pc_human in scene …``.
    aliases: list[str] = Field(default_factory=list)


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


# ------------------------------------------------------------------ #
# Scene-decoration commands (anchor pins, etc).
# ------------------------------------------------------------------ #

class CreateNoteCommand(BaseCommand):
    """Drop an idempotent journal-pin note on a Foundry scene.

    Used at startup to project campaign-pack anchors as in-world map
    pins so ``move_actor_to "valley overlook"`` resolves via the
    Foundry-side ``findTargetOnScene`` (which searches notes by name).
    Idempotent on the JS side: re-running with the same ``text`` reuses
    the existing pin instead of creating a duplicate.
    """
    type: Literal["create_note"] = "create_note"
    scene_id: str | None = None  # default: active scene
    x: int = 0
    y: int = 0
    text: str
    icon: str | None = None


GameCommand = Union[
    MoveTokenCommand,
    MoveActorToCommand,
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
    CreateNoteCommand,
]