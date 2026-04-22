"""
Tiny LLM client.

Two modes:

* **Live**: when ``OPENAI_API_KEY`` is set in the environment, calls
  the OpenAI Chat Completions API in JSON mode and returns the parsed
  JSON object. The model name comes from ``OPENAI_MODEL`` (default
  ``gpt-4o-mini``).
* **Stub**: when no key is present, or when an explicit
  ``canned_response`` is provided, returns that canned payload. This
  keeps the test suite hermetic and lets dev mode work offline.

Contract:

    generate(prompt, *, response_schema=None) -> dict | str
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("ai_dm.ai.client")

_DEFAULT_MODEL = "gpt-5.4-nano"

_FALLBACK_CANNED: dict[str, Any] = {
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


class AIClient:
    def __init__(
        self,
        *,
        canned_response: dict | str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.8,
    ) -> None:
        self._canned = canned_response
        self._model = model or os.environ.get("OPENAI_MODEL") or _DEFAULT_MODEL
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._temperature = temperature
        self._openai = None  # lazy

    # ------------------------------------------------------------------ #

    def generate(
        self,
        prompt: str,
        *,
        response_schema: dict | None = None,
    ) -> Any:
        # Explicit override always wins (tests).
        if self._canned is not None:
            return self._canned

        # No API key → stay offline with the canned fallback.
        if not self._api_key:
            logger.debug("no OPENAI_API_KEY set; returning canned response")
            return _FALLBACK_CANNED

        try:
            return self._call_openai(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenAI call failed (%s); falling back to canned", exc)
            return _FALLBACK_CANNED

    # ------------------------------------------------------------------ #

    def _call_openai(self, prompt: str) -> dict:
        client = self._get_openai_client()
        resp = client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=self._temperature,
        )
        text = resp.choices[0].message.content or "{}"
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("LLM returned non-JSON (%s); raw=%r", exc, text[:200])
            # Return as a minimal AIOutput-shaped dict so the parser can
            # at least surface the prose.
            return {
                "schema_version": "2.0",
                "narration": text,
                "spoken_dialogue": "",
                "dialogue": [],
                "dice_requests": [],
                "commands": [],
                "state_updates": [],
                "metadata": {"parse_error": str(exc)},
            }

    def _get_openai_client(self):
        if self._openai is not None:
            return self._openai
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "openai package not installed; run `pip install openai`"
            ) from exc
        self._openai = OpenAI(api_key=self._api_key)
        return self._openai

