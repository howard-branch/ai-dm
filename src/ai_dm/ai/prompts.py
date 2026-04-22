from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DEFAULT_SYSTEM_PROMPT = (
    "You are the AI Dungeon Master. Respond with a single JSON object that"
    " conforms to the provided output schema. Do not include any prose"
    " outside of the JSON object."
)


def build_narrator_prompt(
    player_input: str,
    context: dict,
    *,
    system_path: Path | None = None,
    schema_path: Path | None = None,
) -> str:
    system = _read_text(system_path) or _DEFAULT_SYSTEM_PROMPT
    schema_section = ""
    if schema_path and schema_path.exists():
        schema_section = (
            "\n\n## Output schema\n```json\n"
            + schema_path.read_text(encoding="utf-8").strip()
            + "\n```"
        )

    return (
        f"{system}{schema_section}\n\n"
        f"## Context\n```json\n{_safe_json(context)}\n```\n\n"
        f"## Player input\n{player_input}\n\n"
        f"Respond with one JSON object."
    )


def _safe_json(payload: Any) -> str:
    try:
        return json.dumps(payload, indent=2, default=str)
    except Exception:  # noqa: BLE001
        return repr(payload)


def _read_text(path: Path | None) -> str | None:
    if path and path.exists():
        return path.read_text(encoding="utf-8").strip()
    return None
