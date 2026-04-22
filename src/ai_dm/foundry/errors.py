"""Errors raised by the Foundry runtime layer."""
from __future__ import annotations

from typing import Any


class FoundryRuntimeError(Exception):
    """Base class for runtime errors in the Foundry pipeline."""


class RegistryMissError(FoundryRuntimeError):
    def __init__(self, kind: str, key: str, scene_id: str | None = None) -> None:
        self.kind = kind
        self.key = key
        self.scene_id = scene_id
        scope = f" (scene={scene_id})" if scene_id else ""
        super().__init__(f"No registered {kind}{scope} for key: {key!r}")


class ValidationError(FoundryRuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_command",
        field: str | None = None,
        command_type: str | None = None,
    ) -> None:
        self.code = code
        self.field = field
        self.command_type = command_type
        super().__init__(message)


class QueueFullError(FoundryRuntimeError):
    pass


class BatchExecutionError(FoundryRuntimeError):
    def __init__(self, message: str, *, results: Any = None, rolled_back: Any = None) -> None:
        self.results = results
        self.rolled_back = rolled_back
        super().__init__(message)


class RollbackError(FoundryRuntimeError):
    pass

