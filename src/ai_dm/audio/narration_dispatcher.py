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
        narration = (payload.get("narration") or "").strip()
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
        spoken = (payload.get("spoken_dialogue") or "").strip()
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

