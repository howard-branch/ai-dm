"""
Wire protocol primitives shared between the Python client, the relay server,
and the Foundry-side socket bridge.

All command/result envelopes flowing through the relay MUST carry a
``request_id`` so that responses can be correlated and duplicates rejected.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, TypedDict

REQUEST_ID_PREFIX = "req-"


def new_request_id() -> str:
    """Generate a fresh, globally-unique request id."""
    return f"{REQUEST_ID_PREFIX}{uuid.uuid4().hex}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CommandEnvelope(TypedDict, total=False):
    type: Literal["command"]
    request_id: str
    command: dict[str, Any]
    correlation_id: str
    issued_at: str


class BatchEnvelope(TypedDict, total=False):
    type: Literal["batch"]
    request_id: str
    commands: list[dict[str, Any]]
    correlation_id: str
    issued_at: str


class ResultEnvelope(TypedDict, total=False):
    type: Literal["result"]
    request_id: str
    command_type: str
    ok: bool
    result: Any
    error: str


def build_command_envelope(
    command: dict[str, Any],
    *,
    request_id: str | None = None,
    correlation_id: str | None = None,
) -> CommandEnvelope:
    env: CommandEnvelope = {
        "type": "command",
        "request_id": request_id or new_request_id(),
        "command": command,
        "issued_at": now_iso(),
    }
    if correlation_id is not None:
        env["correlation_id"] = correlation_id
    return env


def build_batch_envelope(
    commands: Iterable[dict[str, Any]],
    *,
    request_id: str | None = None,
    correlation_id: str | None = None,
) -> BatchEnvelope:
    env: BatchEnvelope = {
        "type": "batch",
        "request_id": request_id or new_request_id(),
        "commands": list(commands),
        "issued_at": now_iso(),
    }
    if correlation_id is not None:
        env["correlation_id"] = correlation_id
    return env


def is_valid_request_id(value: Any) -> bool:
    return isinstance(value, str) and len(value) > 0

