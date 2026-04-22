"""Per-NPC voice profile registry.

Loads ``voices.json`` from the active campaign pack (if present) into a
simple ``npc_id -> voice`` map. Falls back to the configured default
voice when an NPC has no entry.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("ai_dm.audio.voices")


class VoiceProfile:
    def __init__(
        self,
        *,
        default_voice: str = "en-GB-SoniaNeural",
        narrator_voice: str | None = None,
        npc_voices: dict[str, str] | None = None,
    ) -> None:
        self.default_voice = default_voice
        self.narrator_voice = narrator_voice or default_voice
        self.npc_voices: dict[str, str] = dict(npc_voices or {})

    def for_npc(self, npc_id: str | None) -> str:
        if npc_id and npc_id in self.npc_voices:
            return self.npc_voices[npc_id]
        return self.default_voice

    def for_narrator(self) -> str:
        return self.narrator_voice

    @classmethod
    def load(cls, path: Path | None, *, default_voice: str = "en-GB-SoniaNeural") -> "VoiceProfile":
        if path is None or not Path(path).exists():
            return cls(default_voice=default_voice)
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("voices.json unreadable: %s", exc)
            return cls(default_voice=default_voice)
        return cls(
            default_voice=data.get("default", default_voice),
            narrator_voice=data.get("narrator"),
            npc_voices=data.get("npcs", {}),
        )

