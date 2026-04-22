"""
Schema + semantic validator for commands flowing into the Foundry pipeline.

Two responsibilities:

1. Schema validation via the pydantic ``GameCommand`` discriminated union.
2. Semantic validation against a :class:`FoundryRegistry` (entities exist),
   plus per-command rules (allow-listed actor patch keys, non-empty names,
   positive dimensions, etc.).

Every failure raises :class:`ai_dm.foundry.errors.ValidationError` with a
machine-readable ``code`` and the offending ``field``.
"""
from __future__ import annotations

from typing import Any, Iterable

from pydantic import TypeAdapter, ValidationError as PydanticValidationError

from ai_dm.foundry.errors import RegistryMissError, ValidationError
from ai_dm.foundry.registry import FoundryRegistry
from ai_dm.models.commands import (
    ActivateSceneCommand,
    CreateActorCommand,
    CreateSceneCommand,
    DeleteActorCommand,
    DeleteSceneCommand,
    DeleteTokenCommand,
    GameCommand,
    HighlightObjectCommand,
    MoveTokenCommand,
    ReadActiveSceneCommand,
    ReadActorCommand,
    ReadTokenCommand,
    SpawnTokenCommand,
    UpdateActorCommand,
)

DEFAULT_ACTOR_PATCH_ALLOW: tuple[str, ...] = (
    "name",
    "img",
    "system.attributes.",
    "system.details.",
    "system.traits.",
)


class CommandValidator:
    def __init__(
        self,
        registry: FoundryRegistry,
        *,
        actor_patch_allow: Iterable[str] = DEFAULT_ACTOR_PATCH_ALLOW,
        max_coordinate: int | None = 20_000,
    ) -> None:
        self.registry = registry
        self._adapter: TypeAdapter[GameCommand] = TypeAdapter(GameCommand)
        self._actor_patch_allow = tuple(actor_patch_allow)
        self._max_coordinate = max_coordinate

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def validate(self, raw: dict[str, Any] | GameCommand) -> GameCommand:
        cmd = self._coerce(raw)
        self._semantic_check(cmd)
        return cmd

    def validate_many(self, raws: Iterable[dict[str, Any] | GameCommand]) -> list[GameCommand]:
        return [self.validate(r) for r in raws]

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _coerce(self, raw: dict[str, Any] | GameCommand) -> GameCommand:
        if hasattr(raw, "model_dump") and not isinstance(raw, dict):
            # Already a pydantic model — re-validate to be safe.
            raw = raw.model_dump()
        try:
            return self._adapter.validate_python(raw)
        except PydanticValidationError as exc:
            command_type = (raw or {}).get("type") if isinstance(raw, dict) else None
            raise ValidationError(
                f"schema validation failed: {exc.errors(include_url=False)}",
                code="schema",
                command_type=command_type,
            ) from exc

    def _semantic_check(self, cmd: GameCommand) -> None:
        if isinstance(cmd, MoveTokenCommand):
            scene_id = cmd.scene_id
            try:
                cmd.token_id = self.registry.resolve(
                    "token", cmd.token_id, scene_id=scene_id
                )
            except RegistryMissError as exc:
                raise ValidationError(
                    str(exc),
                    code="unknown_token",
                    field="token_id",
                    command_type=cmd.type,
                ) from exc
            self._check_coords(cmd.x, cmd.y, command_type=cmd.type)
            return

        if isinstance(cmd, ActivateSceneCommand):
            cmd.scene_id = self._resolve("scene", cmd.scene_id, "scene_id", cmd.type)
            return

        if isinstance(cmd, UpdateActorCommand):
            cmd.actor_id = self._resolve("actor", cmd.actor_id, "actor_id", cmd.type)
            self._check_actor_patch(cmd.patch, command_type=cmd.type)
            return

        if isinstance(cmd, HighlightObjectCommand):
            # Best-effort resolve as token, then actor; allow opaque ids.
            for kind in ("token", "actor"):
                try:
                    cmd.target_id = self.registry.resolve(kind, cmd.target_id)
                    return
                except RegistryMissError:
                    continue
            return  # leave unresolved, foundry will best-effort canvas search

        if isinstance(cmd, CreateSceneCommand):
            self._require_nonempty(cmd.name, "name", cmd.type)
            self._require_positive(cmd.width, "width", cmd.type)
            self._require_positive(cmd.height, "height", cmd.type)
            self._require_positive(cmd.grid, "grid", cmd.type)
            return

        if isinstance(cmd, CreateActorCommand):
            self._require_nonempty(cmd.name, "name", cmd.type)
            self._require_nonempty(cmd.actor_type, "actor_type", cmd.type)
            return

        if isinstance(cmd, SpawnTokenCommand):
            cmd.scene_id = self._resolve("scene", cmd.scene_id, "scene_id", cmd.type)
            cmd.actor_id = self._resolve("actor", cmd.actor_id, "actor_id", cmd.type)
            self._check_coords(cmd.x, cmd.y, command_type=cmd.type)
            return

        if isinstance(cmd, DeleteTokenCommand):
            cmd.scene_id = self._resolve("scene", cmd.scene_id, "scene_id", cmd.type)
            try:
                cmd.token_id = self.registry.resolve(
                    "token", cmd.token_id, scene_id=cmd.scene_id
                )
            except RegistryMissError:
                # Token may have already been deleted; allow through so the
                # JS layer reports `deleted=false`.
                pass
            return

        if isinstance(cmd, DeleteActorCommand):
            cmd.actor_id = self._resolve("actor", cmd.actor_id, "actor_id", cmd.type)
            return

        if isinstance(cmd, DeleteSceneCommand):
            cmd.scene_id = self._resolve("scene", cmd.scene_id, "scene_id", cmd.type)
            return

        if isinstance(cmd, ReadTokenCommand):
            cmd.scene_id = self._resolve("scene", cmd.scene_id, "scene_id", cmd.type)
            try:
                cmd.token_id = self.registry.resolve(
                    "token", cmd.token_id, scene_id=cmd.scene_id
                )
            except RegistryMissError:
                pass
            return

        if isinstance(cmd, ReadActorCommand):
            cmd.actor_id = self._resolve("actor", cmd.actor_id, "actor_id", cmd.type)
            return

        if isinstance(cmd, ReadActiveSceneCommand):
            return

        if isinstance(cmd, CreateJournalCommand):
            self._require_nonempty(cmd.title, "title", cmd.type)
            return

        if isinstance(cmd, UpdateJournalCommand):
            self._require_nonempty(cmd.journal_id, "journal_id", cmd.type)
            if cmd.title is None and cmd.content is None:
                raise ValidationError(
                    "update_journal requires title or content",
                    code="empty_patch",
                    field="patch",
                    command_type=cmd.type,
                )
            return

    # ------------------------------------------------------------------ #
    # Field helpers
    # ------------------------------------------------------------------ #

    def _resolve(
        self,
        kind: str,
        key: str,
        field: str,
        command_type: str,
    ) -> str:
        try:
            return self.registry.resolve(kind, key)  # type: ignore[arg-type]
        except RegistryMissError as exc:
            raise ValidationError(
                str(exc),
                code=f"unknown_{kind}",
                field=field,
                command_type=command_type,
            ) from exc

    def _check_coords(self, x: int, y: int, *, command_type: str) -> None:
        for name, value in (("x", x), ("y", y)):
            if value < 0:
                raise ValidationError(
                    f"{name} must be >= 0",
                    code="bad_coordinates",
                    field=name,
                    command_type=command_type,
                )
            if self._max_coordinate is not None and value > self._max_coordinate:
                raise ValidationError(
                    f"{name} exceeds max coordinate {self._max_coordinate}",
                    code="bad_coordinates",
                    field=name,
                    command_type=command_type,
                )

    def _check_actor_patch(self, patch: dict, *, command_type: str) -> None:
        if not isinstance(patch, dict) or not patch:
            raise ValidationError(
                "patch must be a non-empty object",
                code="empty_patch",
                field="patch",
                command_type=command_type,
            )
        for key in patch.keys():
            if not any(
                key == allow.rstrip(".") or key.startswith(allow)
                for allow in self._actor_patch_allow
            ):
                raise ValidationError(
                    f"patch key not allowed: {key!r}",
                    code="forbidden_patch_key",
                    field=f"patch.{key}",
                    command_type=command_type,
                )

    @staticmethod
    def _require_nonempty(value: str, field: str, command_type: str) -> None:
        if not value or not str(value).strip():
            raise ValidationError(
                f"{field} must be non-empty",
                code="empty_field",
                field=field,
                command_type=command_type,
            )

    @staticmethod
    def _require_positive(value: int, field: str, command_type: str) -> None:
        if value <= 0:
            raise ValidationError(
                f"{field} must be > 0",
                code="non_positive",
                field=field,
                command_type=command_type,
            )

