"""
Tiny stub LLM client.

In tests / dev mode this returns a canned dict; the real implementation
will be a thin wrapper over an actual LLM SDK. The contract is:

    generate(prompt, *, response_schema=None) -> dict | str
"""
from __future__ import annotations

from typing import Any


class AIClient:
    def __init__(self, *, canned_response: dict | str | None = None) -> None:
        self._canned = canned_response

    def generate(
        self,
        prompt: str,
        *,
        response_schema: dict | None = None,
    ) -> Any:
        if self._canned is not None:
            return self._canned
        return {
            "schema_version": "2.0",
            "narration": "A cold wind moves through the corridor.",
            "spoken_dialogue": "",
            "dialogue": [],
            "dice_requests": [],
            "commands": [
                {
                    "type": "highlight_object",
                    "target_id": "sigil_door",
                }
            ],
            "state_updates": [
                {
                    "type": "set_flag",
                    "key": "noticed_sigil_door",
                    "value": True,
                }
            ],
            "metadata": {"mood": "uneasy"},
        }