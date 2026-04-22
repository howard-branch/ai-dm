"""Append-only world timeline.

A simple list of dated entries that the planner uses to detect "what
has the party already done?". Persisted alongside :class:`ArcState` in
``CampaignSnapshot.timeline``.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from ai_dm.utils.time import now_iso


@dataclass
class TimelineEntry:
    ts: str
    kind: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "kind": self.kind,
            "summary": self.summary,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TimelineEntry":
        return cls(
            ts=data.get("ts") or now_iso(),
            kind=str(data.get("kind") or "event"),
            summary=str(data.get("summary") or ""),
            payload=dict(data.get("payload") or {}),
        )


class Timeline:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: list[TimelineEntry] = []

    def record(self, kind: str, summary: str, payload: dict | None = None) -> TimelineEntry:
        entry = TimelineEntry(ts=now_iso(), kind=kind, summary=summary, payload=payload or {})
        with self._lock:
            self._entries.append(entry)
        return entry

    def all(self) -> list[TimelineEntry]:
        with self._lock:
            return list(self._entries)

    def recent(self, n: int = 10) -> list[TimelineEntry]:
        with self._lock:
            return list(self._entries[-n:])

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [e.to_dict() for e in self._entries]

    def restore(self, data: list[dict] | None) -> None:
        with self._lock:
            self._entries = [TimelineEntry.from_dict(d) for d in (data or [])]

