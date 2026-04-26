"""Persistent conversation/transcript log.

Subscribes to:

  * ``foundry.player_input`` — what each player typed / said
  * ``narrator.output_ready`` — what the DM narrated (prose + dialogue)

…and appends to two files under ``<state_root>/transcripts/``:

  * ``<session>.jsonl`` — one JSON object per line, lossless, machine-
    readable (timestamp, kind, payload).
  * ``<session>.log``   — human-readable plain text, the format you'd
    expect a "session transcript" to have. Useful for re-reading later.

The session id is derived from the wall-clock at startup
(``YYYYmmdd-HHMMSS``) so each run gets its own file. We never truncate
existing files: a process crash leaves the partial transcript intact.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_dm.orchestration.event_bus import EventBus

logger = logging.getLogger("ai_dm.app.transcript")


class TranscriptLogger:
    """Append player input and narrator output to a per-session file."""

    def __init__(
        self,
        *,
        event_bus: EventBus,
        state_root: Path,
        session_id: str | None = None,
    ) -> None:
        self.event_bus = event_bus
        self.state_root = Path(state_root)
        self.session_id = session_id or datetime.now().strftime("%Y%m%d-%H%M%S")
        self._lock = threading.Lock()
        self._unsubs: list = []
        self._dir = self.state_root / "transcripts"
        self._jsonl = self._dir / f"{self.session_id}.jsonl"
        self._log = self._dir / f"{self.session_id}.log"
        self._opened = False

    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._unsubs:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        self._opened = True
        self._write_log_header()
        self._unsubs.append(
            self.event_bus.subscribe("foundry.player_input", self._on_player_input)
        )
        self._unsubs.append(
            self.event_bus.subscribe("narrator.output_ready", self._on_narration)
        )
        logger.info("transcript logger writing to %s", self._log)

    def stop(self) -> None:
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass
        self._unsubs.clear()

    # ------------------------------------------------------------------ #

    @property
    def log_path(self) -> Path:
        return self._log

    @property
    def jsonl_path(self) -> Path:
        return self._jsonl

    # ------------------------------------------------------------------ #

    def _write_log_header(self) -> None:
        with self._lock:
            with self._log.open("a", encoding="utf-8") as fh:
                fh.write(f"\n=== AI DM session {self.session_id} ===\n")

    def _on_player_input(self, payload: dict[str, Any]) -> None:
        text = (payload.get("text") or "").strip()
        if not text:
            return
        actor = payload.get("actor_name") or payload.get("actor_id") or "?"
        user = payload.get("user_name") or payload.get("user_id") or "?"
        self._append_jsonl({
            "kind": "player_input",
            "actor": actor,
            "user": user,
            "scene_id": payload.get("scene_id"),
            "text": text,
        })
        self._append_log(f"[{self._stamp()}] {actor} ({user}): {text}")

    def _on_narration(self, payload: dict[str, Any]) -> None:
        narration = (payload.get("narration") or "").strip()
        dialogue = payload.get("dialogue") or []
        source = payload.get("source", "narrator")
        if not narration and not dialogue:
            return
        self._append_jsonl({
            "kind": "narration",
            "source": source,
            "narration": narration,
            "dialogue": dialogue,
        })
        if narration:
            self._append_log(f"[{self._stamp()}] DM: {narration}")
        for line in dialogue:
            if not isinstance(line, dict):
                continue
            txt = (line.get("text") or "").strip()
            if not txt:
                continue
            who = line.get("npc_id") or "NPC"
            tone = line.get("tone")
            tag = f"{who}" + (f" ({tone})" if tone else "")
            self._append_log(f"[{self._stamp()}] {tag}: {txt}")

    # ------------------------------------------------------------------ #

    def _append_jsonl(self, record: dict[str, Any]) -> None:
        record = {"ts": datetime.now().isoformat(timespec="seconds"), **record}
        with self._lock:
            with self._jsonl.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _append_log(self, line: str) -> None:
        with self._lock:
            with self._log.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    @staticmethod
    def _stamp() -> str:
        return datetime.now().strftime("%H:%M:%S")

