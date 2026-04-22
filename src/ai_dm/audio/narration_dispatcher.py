"""Bridges narrator output to the audio queue.

Subscribes to the ``narrator.output_ready`` event (published by the
:class:`Director`) and pushes one queue item per narration block + per
dialogue line, with the appropriate voice.
"""
from __future__ import annotations

import logging
from typing import Any

from ai_dm.audio.audio_queue import AudioItem, AudioQueue
from ai_dm.audio.voices import VoiceProfile
from ai_dm.orchestration.event_bus import EventBus

logger = logging.getLogger("ai_dm.audio.dispatcher")


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
            self.queue.enqueue(
                AudioItem(
                    text=narration,
                    voice=payload.get("voice") or self.voices.for_narrator(),
                    priority="narration",
                    tag="narration",
                    meta={"source": payload.get("source", "narrator")},
                )
            )
        spoken = (payload.get("spoken_dialogue") or "").strip()
        if spoken:
            self.queue.enqueue(
                AudioItem(
                    text=spoken,
                    voice=self.voices.for_narrator(),
                    priority="narration",
                    tag="spoken_dialogue",
                )
            )
        for line in payload.get("dialogue", []) or []:
            text = (line.get("text") if isinstance(line, dict) else None) or ""
            if not text.strip():
                continue
            npc_id = line.get("npc_id") if isinstance(line, dict) else None
            self.queue.enqueue(
                AudioItem(
                    text=text,
                    voice=self.voices.for_npc(npc_id),
                    priority="dialogue",
                    speaker_id=npc_id,
                    tag="dialogue",
                )
            )

