"""Two-stage parser: regex/keyword fast-path + LLM fallback.

The fast path handles the dominant utterances (attack X, move to Y,
roll perception, "I say ...") with high confidence and zero LLM cost.
Anything else is sent to the LLM via :class:`AIClient` (which can be
stubbed in tests).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ai_dm.ai.client import AIClient
from ai_dm.ai.intent_schemas import PlayerIntent
from ai_dm.rules.dnd5e_reference import SKILL_TO_ABILITY

logger = logging.getLogger("ai_dm.intent")

# Roughly: "I attack the goblin", "attack goblin with longsword".
_ATTACK_RE = re.compile(
    r"\b(?:i\s+)?(?:attack|swing\s+at|strike|hit|stab|shoot|fire\s+at)\s+"
    r"(?:the\s+|a\s+|an\s+)?(?P<target>[\w' \-]+?)"
    r"(?:\s+with\s+(?:my\s+|a\s+|the\s+)?(?P<weapon>[\w' \-]+))?\s*[.!?]*$",
    re.IGNORECASE,
)

# "I move to the altar", "go to the door"
_MOVE_RE = re.compile(
    r"\b(?:i\s+)?(?:move|go|walk|run|approach|head)\s+(?:to|toward|towards)\s+"
    r"(?:the\s+|a\s+|an\s+)?(?P<anchor>[\w' \-]+?)\s*[.!?]*$",
    re.IGNORECASE,
)

# "roll perception", "make a stealth check (DC 15)"
_CHECK_RE = re.compile(
    r"\b(?:roll|make\s+a)\s+(?P<skill>" + "|".join(SKILL_TO_ABILITY.keys()) + r")(?:\s+check)?"
    r"(?:[^0-9]*(?P<dc>\d+))?",
    re.IGNORECASE,
)

# Quoted speech: "I say 'hello'", or just `"hello"` / `'hello'`.
_SPEAK_RE = re.compile(r"""(?:i\s+(?:say|tell\s+\w+|whisper|shout))\s*[:,]?\s*['"](?P<q>.+?)['"]""", re.IGNORECASE)
_BARE_QUOTE_RE = re.compile(r"""^\s*['"](?P<q>.+?)['"]\s*$""")

# "use the lantern", "drink potion"
_USE_RE = re.compile(
    r"\b(?:i\s+)?(?:use|drink|read|open|pull|push|throw|drop|pick\s+up)\s+"
    r"(?:the\s+|a\s+|an\s+|my\s+)?(?P<item>[\w' \-]+?)\s*[.!?]*$",
    re.IGNORECASE,
)

# Meta verbs
_META_RE = re.compile(r"^\s*(?:save|load|recap|undo|help)\b", re.IGNORECASE)

_LLM_PROMPT_TEMPLATE = """\
You are a parser. Convert the player's utterance to a JSON object with the
following fields (omit unknowns):

  type: one of {types}
  verb, actor_id, target_id, target_anchor, weapon, skill, spell, dc,
  modifier, quoted_speech, confidence (0..1), notes

Player utterance:
{text}

Respond with one JSON object only, no prose.
"""

_INTENT_TYPES = (
    "move,attack,cast_spell,skill_check,interact,speak,use_item,"
    "query_world,meta,unknown"
)


class IntentParser:
    """Hybrid fast-path + LLM intent parser."""

    def __init__(
        self,
        *,
        client: AIClient | None = None,
        confidence_threshold: float = 0.6,
        default_actor_id: str = "player",
    ) -> None:
        self.client = client
        self.confidence_threshold = confidence_threshold
        self.default_actor_id = default_actor_id

    # ------------------------------------------------------------------ #

    def parse(self, text: str, ctx: dict | None = None) -> PlayerIntent:
        text = (text or "").strip()
        if not text:
            return PlayerIntent(type="unknown", raw_text="", confidence=1.0)

        fast = self._fast_path(text)
        if fast is not None and fast.confidence >= self.confidence_threshold:
            return fast

        if self.client is None:
            # No fallback available; return the best fast-path guess (or unknown).
            return fast or PlayerIntent(
                type="unknown", raw_text=text, confidence=0.0, notes="no LLM available"
            )

        llm_intent = self._llm_path(text, ctx or {})
        # Prefer LLM if it's confident; otherwise keep fast.
        if fast and llm_intent.confidence < fast.confidence:
            return fast
        return llm_intent

    # ------------------------------------------------------------------ #
    # Fast path
    # ------------------------------------------------------------------ #

    def _fast_path(self, text: str) -> PlayerIntent | None:
        if _META_RE.match(text):
            verb = text.strip().split()[0].lower()
            return PlayerIntent(
                type="meta", verb=verb, raw_text=text, confidence=0.95,
                actor_id=self.default_actor_id,
            )

        m = _SPEAK_RE.search(text)
        if m:
            return PlayerIntent(
                type="speak",
                verb="say",
                quoted_speech=m.group("q"),
                raw_text=text,
                confidence=0.9,
                actor_id=self.default_actor_id,
            )
        m = _BARE_QUOTE_RE.match(text)
        if m:
            return PlayerIntent(
                type="speak", verb="say", quoted_speech=m.group("q"),
                raw_text=text, confidence=0.85, actor_id=self.default_actor_id,
            )

        m = _ATTACK_RE.search(text)
        if m:
            return PlayerIntent(
                type="attack",
                verb="attack",
                actor_id=self.default_actor_id,
                target_id=_clean(m.group("target")),
                weapon=_clean(m.group("weapon")) if m.group("weapon") else None,
                raw_text=text,
                confidence=0.85,
            )

        m = _MOVE_RE.search(text)
        if m:
            anchor = _clean(m.group("anchor"))
            return PlayerIntent(
                type="move",
                verb="move",
                actor_id=self.default_actor_id,
                target_anchor=anchor,
                raw_text=text,
                confidence=0.8,
            )

        m = _CHECK_RE.search(text)
        if m:
            dc = int(m.group("dc")) if m.group("dc") else None
            return PlayerIntent(
                type="skill_check",
                verb="check",
                actor_id=self.default_actor_id,
                skill=m.group("skill").lower(),
                dc=dc,
                raw_text=text,
                confidence=0.85,
            )

        m = _USE_RE.search(text)
        if m:
            return PlayerIntent(
                type="use_item",
                verb="use",
                actor_id=self.default_actor_id,
                target_id=_clean(m.group("item")),
                raw_text=text,
                confidence=0.65,
            )

        return None

    # ------------------------------------------------------------------ #
    # LLM path
    # ------------------------------------------------------------------ #

    def _llm_path(self, text: str, ctx: dict) -> PlayerIntent:
        prompt = _LLM_PROMPT_TEMPLATE.format(types=_INTENT_TYPES, text=text)
        try:
            raw = self.client.generate(prompt, response_schema=None)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            logger.warning("intent LLM call failed: %s", exc)
            return PlayerIntent(type="unknown", raw_text=text, confidence=0.0, notes=str(exc))

        payload = _coerce_json(raw)
        if payload is None:
            return PlayerIntent(
                type="unknown", raw_text=text, confidence=0.0,
                notes="LLM returned non-JSON",
            )
        payload.setdefault("raw_text", text)
        payload.setdefault("actor_id", self.default_actor_id)
        try:
            return PlayerIntent.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("intent LLM payload invalid: %s", exc)
            return PlayerIntent(
                type="unknown", raw_text=text, confidence=0.0, notes=f"invalid: {exc}"
            )


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #

def _clean(s: str | None) -> str | None:
    if s is None:
        return None
    s = s.strip().rstrip(".!?,").strip()
    return re.sub(r"\s+", " ", s) or None


def _coerce_json(raw: Any) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        # strip ```json fences
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?", "", text).strip()
            text = re.sub(r"```$", "", text).strip()
        try:
            decoded = json.loads(text)
            return decoded if isinstance(decoded, dict) else None
        except Exception:  # noqa: BLE001
            return None
    return None

