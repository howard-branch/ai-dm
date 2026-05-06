"""Append-only JSONL audit log for every die roll the engine performs.

Both player-driven rolls (resolved in Foundry via the roll-prompt UI)
and DM-side rolls (rolled in Python by :class:`DMRoller`) write through
the same :meth:`RollLog.append` so the audit trail is one stream.

Layout::

    <state_root>/logs/rolls.jsonl

One JSON object per line, never truncated. Safe for concurrent writers
within the same process via an internal lock; cross-process appends are
already line-atomic on POSIX for writes < PIPE_BUF.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("ai_dm.persistence.roll_log")


@dataclass
class RollRecord:
    """Single audit record for a roll (player- or DM-initiated)."""

    request_id: str
    source: str                      # "player" | "dm"
    actor_id: str | None
    roll_type: str                   # skill | save | ability | attack | damage | raw
    key: str | None                  # e.g. "perception", "dex_save", "longsword"
    formula: str
    total: int
    modifier: int = 0
    rolls: list[int] = field(default_factory=list)
    kept: list[int] = field(default_factory=list)
    advantage: str = "normal"
    crit: bool = False
    fumble: bool = False
    dc: int | None = None
    ac: int | None = None
    success: bool | None = None
    visibility: str = "public"       # "public" | "gm" | "self"
    scene_id: str | None = None
    reason: str | None = None
    prompt_text: str | None = None
    elapsed_ms: int | None = None
    ts: str = ""                     # ISO timestamp; filled by RollLog if blank

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None or k in (
            "actor_id", "key", "dc", "ac", "success", "reason",
            "prompt_text", "elapsed_ms", "scene_id",
        )}


class RollLog:
    """Append-only roll audit sink under ``<state_root>/logs/rolls.jsonl``."""

    FILENAME = "rolls.jsonl"

    def __init__(self, *, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self._dir = self.state_root / "logs"
        self._path = self._dir / self.FILENAME
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: RollRecord) -> None:
        """Append a single record. Best-effort: log + swallow on I/O error."""
        if not record.ts:
            record.ts = datetime.now().isoformat(timespec="milliseconds")
        try:
            with self._lock:
                self._dir.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("roll log append failed: %s", exc)

    def iter_records(self) -> Iterable[dict[str, Any]]:
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:  # noqa: BLE001
                    continue

