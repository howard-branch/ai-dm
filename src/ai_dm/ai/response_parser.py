"""
Parses and repairs LLM responses into :class:`AIOutput`.

The narrator may receive raw strings (LLM completion), JSON-mode dicts,
or already-typed AIOutput instances. ``safe_parse_ai_output`` never
raises — on failure it returns a minimal valid ``AIOutput`` with the
problem recorded in ``metadata.parse_errors``.
"""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from ai_dm.ai.schemas import AIOutput

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.+?)\s*```$", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


class ParseIssue(BaseModel):
    kind: str  # "json" | "schema" | "partial" | "empty"
    message: str
    path: str | None = None


def parse_ai_output(payload: dict | str | AIOutput) -> AIOutput:
    """Strict parse. Raises ``ValueError`` or ``ValidationError``."""
    if isinstance(payload, AIOutput):
        return payload
    if isinstance(payload, str):
        payload = _string_to_json(payload)
    return AIOutput.model_validate(payload)


def safe_parse_ai_output(
    payload: dict | str | AIOutput | None,
    *,
    fallback_narration: str = "(The DM pauses, gathering their thoughts.)",
) -> tuple[AIOutput, list[ParseIssue]]:
    """Best-effort parse. Always returns a valid AIOutput.

    Errors are surfaced via the returned ``ParseIssue`` list and also
    embedded in ``output.metadata['parse_errors']``.
    """
    issues: list[ParseIssue] = []

    if payload is None or (isinstance(payload, str) and not payload.strip()):
        return _fallback(fallback_narration, [ParseIssue(kind="empty", message="empty payload")]), issues

    raw_payload: Any = payload
    if isinstance(raw_payload, AIOutput):
        return raw_payload, issues

    if isinstance(raw_payload, str):
        try:
            raw_payload = _string_to_json(raw_payload)
        except ValueError as exc:
            issues.append(ParseIssue(kind="json", message=str(exc)))
            return _fallback(payload if isinstance(payload, str) else fallback_narration, issues), issues

    try:
        output = AIOutput.model_validate(raw_payload)
    except ValidationError as exc:
        for err in exc.errors(include_url=False):
            issues.append(
                ParseIssue(
                    kind="schema",
                    message=err.get("msg", "schema error"),
                    path=".".join(str(p) for p in err.get("loc", ())),
                )
            )
        # Try to coerce a partial dict — keep narration if present.
        narration = ""
        if isinstance(raw_payload, dict):
            narration = str(raw_payload.get("narration") or "")
        output = _fallback(narration or fallback_narration, issues)

    if issues:
        existing = output.metadata.setdefault("parse_errors", [])
        existing.extend([i.model_dump() for i in issues])

    return output, issues


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #

def _string_to_json(text: str) -> dict:
    text = _strip_code_fences(text.strip())
    text = _extract_first_json_object(text)
    text = _repair_trailing_commas(text)
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ValueError("expected a JSON object at top level")
    return decoded


def _strip_code_fences(text: str) -> str:
    m = _CODE_FENCE_RE.match(text)
    return m.group(1) if m else text


def _extract_first_json_object(text: str) -> str:
    """Find the first balanced ``{...}`` block in ``text``.

    Tolerates leading/trailing prose so long as the object braces match.
    """
    start = text.find("{")
    if start == -1:
        return text  # let json.loads raise
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def _repair_trailing_commas(text: str) -> str:
    return _TRAILING_COMMA_RE.sub(r"\1", text)


def _fallback(narration: str, issues: list[ParseIssue]) -> AIOutput:
    return AIOutput(
        narration=narration or "(no narration)",
        metadata={"parse_errors": [i.model_dump() for i in issues]} if issues else {},
    )
