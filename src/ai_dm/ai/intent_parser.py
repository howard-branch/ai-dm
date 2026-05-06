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

# "travel to shadowgrange", "journey north", "set out for the chapel",
# "depart for shadowgrange", "leave for shadowgrange", "head north".
# Travel is a *cross-scene* move (activate_scene + new opening) — the
# in-scene `move` regex below stays, but the verbs above are reserved
# for the travel handler so the parser can disambiguate without
# guessing whether a target is a scene id or an in-scene anchor.
_TRAVEL_RE = re.compile(
    r"\b(?:i\s+)?(?:travel|journey|set\s+(?:out|off)|depart|leave\s+for|march)"
    r"(?:\s+(?:to|toward|towards|for))?\s+"
    r"(?:the\s+|a\s+|an\s+)?(?P<dest>[\w' \-]+?)\s*[.!?]*$",
    re.IGNORECASE,
)

# "I move to the altar", "go to the door", "move_actor_to pass_overlook",
# "walk into the nave", "step onto the dais", "head through the gate".
# (the underscore form lets us round-trip the LLM-emitted command type
# verbatim, and the optional `to` lets the player just say "go altar".)
_MOVE_RE = re.compile(
    r"\b(?:i\s+)?(?:move(?:_actor)?(?:_to)?|goto|go|walk|run|approach|head|step|enter)"
    r"(?:\s+(?:to|toward|towards|into|onto|inside|through|past|across|over\s+to|up\s+to))?\s+"
    r"(?:the\s+|a\s+|an\s+)?(?P<anchor>[\w' \-]+?)\s*[.!?]*$",
    re.IGNORECASE,
)

# Cardinal directions (used by both move and partial-move regexes).
_DIRECTION_TOKENS = (
    r"north(?:east|west)?|south(?:east|west)?|east|west|"
    r"ne|nw|se|sw|n|s|e|w|up|down|left|right"
)

# Partial / directional move:
#   "move 30 feet toward the altar"
#   "advance 15 ft north"
#   "step back 10 feet from the goblin"
#   "walk 20 feet south"
# Captures distance (ft) plus an optional direction word and an
# optional target anchor. ``direction`` is normalised to "toward",
# "away", or a cardinal token; "back from X" maps to "away".
_MOVE_DIST_RE = re.compile(
    r"^\s*(?:i\s+)?"
    r"(?:move|advance|step|walk|run|head|go|sprint|dash|fall\s+back|back\s+up|"
    r"retreat|withdraw)"
    r"(?:\s+back)?"
    r"\s+(?P<dist>\d{1,3})\s*(?:ft|feet|foot|')"
    r"(?:"
    r"\s+(?P<dir_word>toward|towards|to|away\s+from|back\s+from|from)"
    r"\s+(?:the\s+|a\s+|an\s+)?(?P<anchor>[\w' \-]+?)"
    r"|"
    r"\s+(?P<cardinal>" + _DIRECTION_TOKENS + r")"
    r")?"
    r"\s*[.!?]*$",
    re.IGNORECASE,
)

# Reversed phrasing: "move north 10 feet", "go south 20ft", "head west 5'".
# Players say this as often as the distance-first form, but the
# distance-first regex above can't match it; without this variant the
# tail "north 10 feet" would fall through to ``_MOVE_RE`` and be
# captured verbatim as ``target_anchor`` — which then gets shipped to
# Foundry as ``move_actor_to "north 10 feet"`` and fails with
# "target not found on scene".
_MOVE_DIR_DIST_RE = re.compile(
    r"^\s*(?:i\s+)?"
    r"(?:move|advance|step|walk|run|head|go|sprint|dash|fall\s+back|back\s+up|"
    r"retreat|withdraw)"
    r"(?:\s+back)?"
    r"\s+(?P<cardinal>" + _DIRECTION_TOKENS + r")"
    r"\s+(?P<dist>\d{1,3})\s*(?:ft|feet|foot|')"
    r"\s*[.!?]*$",
    re.IGNORECASE,
)

# Party-scope detection. Players phrase whole-party moves a dozen
# different ways: "move whole party to the brink", "take everyone to
# the chapel", "we all head north", "let's go to the door". When any
# of these phrases is present the intent applies to every party
# member, not just the active PC. The router fans the move/travel out
# to one command per party token.
_PARTY_SCOPE_RE = re.compile(
    r"\b(?:whole\s+party|the\s+party|party|everyone|everybody|"
    r"all\s+of\s+us|we\s+all|let'?s|let\s+us|we|us)\b",
    re.IGNORECASE,
)
# Single-pass match for party-scoped move/travel commands. Captures
# the destination ``anchor``. Accepts an optional outer verb
# (``move|take|lead|bring|send|march``), the party-scope token, an
# optional inner verb (``go|head|move|walk|run|...``), an optional
# preposition (``to|toward|...``) and an optional article. Examples
# matched (with capture):
#   "move whole party to the brink"     -> "brink"
#   "take everyone to the chapel"       -> "chapel"
#   "lead us to the altar"              -> "altar"
#   "we head north"                     -> "north"
#   "let's go to the door"              -> "door"
#   "we all march to shadowgrange"      -> "shadowgrange"
_PARTY_MOVE_RE = re.compile(
    r"^\s*"
    r"(?:(?:i\s+)?(?:move|take|lead|bring|send|march)\s+)?"
    r"(?:let'?s\s+|let\s+us\s+)?"
    r"(?:the\s+)?(?:whole\s+)?"
    r"(?:party|everyone|everybody|all\s+of\s+us|us|we(?:\s+all)?)\s+"
    r"(?:(?:go|head|move|walk|run|approach|march|travel|journey|gather|regroup|assemble|step|enter)\s+)?"
    r"(?:to|toward|towards|into|onto|inside|through|past|across|over\s+to|up\s+to|for|at)?\s*"
    r"(?:the\s+|a\s+|an\s+)?"
    r"(?P<anchor>[\w' \-]+?)\s*[.!?]*$",
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

# Meta verbs. Matches "save", "load", "recap", "undo", or a *bare*
# "help" (with no following target). "help <name>" is the combat
# Help action and is handled separately below.
_META_RE = re.compile(
    r"^\s*(?:save|load|recap|undo|help)\s*[.!?]*\s*$",
    re.IGNORECASE,
)

# Combat action menu (5e SRD). These are matched before generic
# verb regexes so "I help Vex" doesn't slip into the use/attack path.
_DASH_RE = re.compile(r"^\s*(?:i\s+)?dash\b\s*[.!?]*$", re.IGNORECASE)
_DISENGAGE_RE = re.compile(r"^\s*(?:i\s+)?disengage\b\s*[.!?]*$", re.IGNORECASE)
_DODGE_RE = re.compile(r"^\s*(?:i\s+)?dodge\b\s*[.!?]*$", re.IGNORECASE)
_HIDE_RE = re.compile(r"^\s*(?:i\s+)?hide\b\s*[.!?]*$", re.IGNORECASE)
_END_TURN_RE = re.compile(
    r"^\s*(?:i\s+)?(?:end\s+turn|end\s+my\s+turn|done|pass)\b\s*[.!?]*$",
    re.IGNORECASE,
)
_HELP_TARGET_RE = re.compile(
    r"^\s*(?:i\s+)?help\s+(?:the\s+|a\s+|an\s+)?(?P<target>[\w' \-]+?)\s*[.!?]*$",
    re.IGNORECASE,
)
_READY_RE = re.compile(
    r"^\s*(?:i\s+)?ready\s+(?:an?\s+)?(?P<sub>attack|action|spell|cast)\b"
    r"(?:\s+(?:to|when|if)\s+(?P<trigger>.+?))?\s*[.!?]*$",
    re.IGNORECASE,
)
_CAST_RE = re.compile(
    r"^\s*(?:i\s+)?(?:cast|invoke)\s+(?:the\s+)?(?P<spell>[\w' \-]+?)"
    r"(?:\s+(?:on|at)\s+(?:the\s+|a\s+|an\s+)?(?P<target>[\w' \-]+?))?\s*[.!?]*$",
    re.IGNORECASE,
)

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
    "move,travel,attack,cast_spell,skill_check,interact,speak,use_item,"
    "dash,disengage,dodge,help,hide,ready,end_turn,"
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

        # Combat action menu — single-word verbs first.
        if _DASH_RE.match(text):
            return PlayerIntent(
                type="dash", verb="dash", actor_id=self.default_actor_id,
                raw_text=text, confidence=0.95,
            )
        if _DISENGAGE_RE.match(text):
            return PlayerIntent(
                type="disengage", verb="disengage", actor_id=self.default_actor_id,
                raw_text=text, confidence=0.95,
            )
        if _DODGE_RE.match(text):
            return PlayerIntent(
                type="dodge", verb="dodge", actor_id=self.default_actor_id,
                raw_text=text, confidence=0.95,
            )
        if _HIDE_RE.match(text):
            return PlayerIntent(
                type="hide", verb="hide", actor_id=self.default_actor_id,
                raw_text=text, confidence=0.9,
            )
        if _END_TURN_RE.match(text):
            return PlayerIntent(
                type="end_turn", verb="end_turn", actor_id=self.default_actor_id,
                raw_text=text, confidence=0.95,
            )
        m = _HELP_TARGET_RE.match(text)
        if m:
            return PlayerIntent(
                type="help", verb="help", actor_id=self.default_actor_id,
                target_id=_clean(m.group("target")),
                raw_text=text, confidence=0.85,
            )
        m = _READY_RE.match(text)
        if m:
            sub = (m.group("sub") or "").lower()
            sub_action = "cast_spell" if sub in ("spell", "cast") else "attack"
            extra = {"action": sub_action}
            if m.group("trigger"):
                extra["trigger"] = _clean(m.group("trigger")) or ""
            return PlayerIntent(
                type="ready", verb="ready", actor_id=self.default_actor_id,
                raw_text=text, confidence=0.85, extra=extra,
            )
        m = _CAST_RE.match(text)
        if m:
            return PlayerIntent(
                type="cast_spell", verb="cast",
                actor_id=self.default_actor_id,
                spell=_clean(m.group("spell")),
                target_id=_clean(m.group("target")) if m.group("target") else None,
                raw_text=text, confidence=0.85,
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

        m = _TRAVEL_RE.search(text)
        if m:
            dest = _clean(m.group("dest"))
            return PlayerIntent(
                type="travel",
                verb="travel",
                actor_id=self.default_actor_id,
                target_id=dest,
                target_anchor=dest,
                raw_text=text,
                confidence=0.85,
                extra={"party_scope": True} if _PARTY_SCOPE_RE.search(text) else {},
            )

        # Party-scope move/travel: "move whole party to the brink",
        # "take everyone to the chapel", "we head north". Matched
        # before the single-actor _MOVE_RE so the party-scope phrasing
        # doesn't accidentally bind to a single PC. The router maps
        # ``extra.party_scope`` to a fan-out across every party token.
        m = _PARTY_MOVE_RE.search(text)
        if m:
            anchor = _clean(m.group("anchor"))
            return PlayerIntent(
                type="move",
                verb="move",
                actor_id=self.default_actor_id,
                target_anchor=anchor,
                raw_text=text,
                confidence=0.85,
                extra={"party_scope": True},
            )

        # Partial / directional move (matched before the generic
        # _MOVE_RE so "move 30 feet toward altar" doesn't collapse
        # into target_anchor="30 feet toward altar"). Try the
        # direction-first phrasing ("move north 10 feet") first so
        # the cardinal token isn't swallowed by the generic anchor
        # group of _MOVE_RE.
        m = _MOVE_DIR_DIST_RE.match(text)
        if m:
            try:
                dist = int(m.group("dist"))
            except (TypeError, ValueError):
                dist = None
            cardinal = (m.group("cardinal") or "").lower() or None
            return PlayerIntent(
                type="move",
                verb="move",
                actor_id=self.default_actor_id,
                target_anchor=None,
                distance_ft=dist,
                direction=cardinal,
                raw_text=text,
                confidence=0.9,
                extra=({"party_scope": True}
                       if _PARTY_SCOPE_RE.search(text) else {}),
            )

        m = _MOVE_DIST_RE.match(text)
        if m:
            try:
                dist = int(m.group("dist"))
            except (TypeError, ValueError):
                dist = None
            anchor_raw = m.group("anchor") if m.groupdict().get("anchor") else None
            cardinal = (m.group("cardinal") if m.groupdict().get("cardinal") else None) or None
            dir_word = (m.group("dir_word") if m.groupdict().get("dir_word") else None) or None
            anchor = _clean(anchor_raw) if anchor_raw else None
            direction: str | None = None
            if cardinal:
                direction = cardinal.lower()
            elif dir_word:
                dw = dir_word.lower().strip()
                if "away" in dw or "back" in dw or dw == "from":
                    direction = "away"
                else:
                    direction = "toward"
            elif anchor:
                direction = "toward"
            return PlayerIntent(
                type="move",
                verb="move",
                actor_id=self.default_actor_id,
                target_anchor=anchor,
                distance_ft=dist,
                direction=direction,
                raw_text=text,
                confidence=0.9,
                extra=({"party_scope": True}
                       if _PARTY_SCOPE_RE.search(text) else {}),
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
                # Catch party-scope phrasings that the dedicated
                # _PARTY_MOVE_RE missed (e.g. "let's go to the door"
                # — verb "go" is matched by _MOVE_RE first, but the
                # surrounding "let's" / "us" still implies the whole
                # party should move).
                extra=({"party_scope": True}
                       if _PARTY_SCOPE_RE.search(text) else {}),
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

