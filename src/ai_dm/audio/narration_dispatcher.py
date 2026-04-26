"""Bridges narrator output to the audio queue.

Subscribes to the ``narrator.output_ready`` event (published by the
:class:`Director`) and pushes one queue item per narration block + per
dialogue line, with the appropriate voice.

Long narration blocks are split on sentence boundaries before being
enqueued. The audio queue is serial, so this means:

* The first (small) sentence is synthesised and starts playing
  significantly sooner than if we waited for one big TTS request.
* The user can interrupt mid-narration with finer granularity.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from ai_dm.audio.audio_queue import AudioItem, AudioQueue
from ai_dm.audio.voices import VoiceProfile
from ai_dm.orchestration.event_bus import EventBus

logger = logging.getLogger("ai_dm.audio.dispatcher")

# Split on sentence-ending punctuation followed by whitespace. Keep the
# punctuation attached to the preceding sentence. We deliberately keep
# this simple — it's just a latency optimisation, perfect tokenisation
# is not required.
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÀ-ÖØ-Þ\"'“‘(\[])")

# Strip Foundry-style identifiers from text before TTS. Foundry's
# ``randomID`` produces 16-char base62 tokens (mixed case + digits) that
# leak into narration when the LLM quotes a context field verbatim
# (e.g. "your character (id: aB7xQ2pM9KvLnR4t)"). Reading them aloud is
# noise, so we drop:
#   * any 12+ char run of [A-Za-z0-9] containing at least one digit AND
#     at least one upper-case letter (heuristic for opaque ids),
#   * common framing like ``(id: …)`` / ``[id: …]``.
_ID_FRAME_RE = re.compile(
    r"\s*[\(\[]\s*(?:id|actor[_ ]?id|user[_ ]?id|token[_ ]?id|scene[_ ]?id)"
    r"\s*[:=]\s*[A-Za-z0-9_-]+\s*[\)\]]",
    re.IGNORECASE,
)
_BARE_ID_RE = re.compile(
    r"\b(?=[A-Za-z0-9]*[0-9])(?=[A-Za-z0-9]*[A-Z])[A-Za-z0-9]{12,}\b"
)


def _strip_ids_for_speech(text: str) -> str:
    """Remove opaque Foundry-style identifiers from spoken text.

    Keeps the surrounding prose readable: the framing ``(id: …)`` is
    dropped wholesale, and bare hashes are replaced with a placeholder
    so a sentence like ``"You are aB7xQ2pM9KvLnR4t"`` becomes
    ``"You are someone"`` rather than collapsing into a malformed run.
    """
    if not text:
        return text
    out = _ID_FRAME_RE.sub("", text)
    out = _BARE_ID_RE.sub("someone", out)
    # Collapse double-spaces left by removed framings.
    out = re.sub(r"[ \t]{2,}", " ", out).strip()
    return out


def _split_sentences(text: str, *, max_chars: int = 240) -> list[str]:
    """Split prose into TTS-friendly chunks.

    Sentence boundaries first; any chunk still longer than ``max_chars``
    is further split on commas / semicolons so we never hand the TTS a
    huge blob (which would delay first audio).
    """
    text = text.strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENTENCE_RE.split(text) if p.strip()]
    out: list[str] = []
    for p in parts:
        if len(p) <= max_chars:
            out.append(p)
            continue
        # Secondary split on , ; — keep delimiters with the chunk before.
        sub = re.split(r"(?<=[,;:])\s+", p)
        buf = ""
        for s in sub:
            if not buf:
                buf = s
            elif len(buf) + 1 + len(s) <= max_chars:
                buf = f"{buf} {s}"
            else:
                out.append(buf)
                buf = s
        if buf:
            out.append(buf)
    return out


class NarrationDispatcher:
    def __init__(
        self,
        *,
        event_bus: EventBus,
        queue: AudioQueue,
        voices: VoiceProfile,
        enabled: bool = True,
    ) -> None:
        self.event_bus = event_bus
        self.queue = queue
        self.voices = voices
        self.enabled = enabled
        self._unsub = None

    def start(self) -> None:
        if self._unsub is not None:
            return
        self._unsub = self.event_bus.subscribe("narrator.output_ready", self._on_output)

    def stop(self) -> None:
        if self._unsub is not None:
            try:
                self._unsub()
            except Exception:  # noqa: BLE001
                pass
            self._unsub = None

    # ------------------------------------------------------------------ #

    def _on_output(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        narration = _strip_ids_for_speech((payload.get("narration") or "").strip())
        if narration:
            voice = payload.get("voice") or self.voices.for_narrator()
            source = payload.get("source", "narrator")
            sentences = _split_sentences(narration)
            for idx, sentence in enumerate(sentences):
                self.queue.enqueue(
                    AudioItem(
                        text=sentence,
                        voice=voice,
                        priority="narration",
                        tag="narration",
                        meta={
                            "source": source,
                            "chunk_index": idx,
                            "chunk_count": len(sentences),
                        },
                    )
                )
        spoken = _strip_ids_for_speech((payload.get("spoken_dialogue") or "").strip())
        if spoken:
            voice = self.voices.for_narrator()
            for sentence in _split_sentences(spoken):
                self.queue.enqueue(
                    AudioItem(
                        text=sentence,
                        voice=voice,
                        priority="narration",
                        tag="spoken_dialogue",
                    )
                )
        for line in payload.get("dialogue", []) or []:
            text = (line.get("text") if isinstance(line, dict) else None) or ""
            text = _strip_ids_for_speech(text)
            if not text.strip():
                continue
            npc_id = line.get("npc_id") if isinstance(line, dict) else None
            voice = self.voices.for_npc(npc_id)
            for sentence in _split_sentences(text):
                self.queue.enqueue(
                    AudioItem(
                        text=sentence,
                        voice=voice,
                        priority="dialogue",
                        speaker_id=npc_id,
                        tag="dialogue",
                    )
                )

