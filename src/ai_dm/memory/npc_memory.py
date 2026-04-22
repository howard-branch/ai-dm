"""Per-NPC memory store with bounded ring buffer and summarisation hook."""
from __future__ import annotations

import threading
from collections import deque
from typing import Callable, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_dm.utils.time import now_iso


MemoryKind = Literal["event", "fact", "rumor"]


class MemoryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts: str = Field(default_factory=now_iso)
    kind: MemoryKind = "event"
    text: str
    tags: list[str] = Field(default_factory=list)
    salience: float = 0.5  # 0..1


class NPCMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str
    summary: str = ""
    events: list[MemoryEvent] = Field(default_factory=list)


Summariser = Callable[[list[MemoryEvent]], str]


class NPCMemoryStore:
    """In-memory ring of recent events per NPC.

    Facts (``kind="fact"``) are kept indefinitely; events past
    ``max_events_per_npc`` are summarised via the optional ``summariser``
    callback and discarded.
    """

    def __init__(
        self,
        *,
        max_events_per_npc: int = 50,
        summariser: Summariser | None = None,
    ) -> None:
        self.max_events_per_npc = max_events_per_npc
        self.summariser = summariser
        self._lock = threading.RLock()
        self._memories: dict[str, NPCMemory] = {}

    # ------------------------------------------------------------------ #

    def record(self, npc_id: str, event: MemoryEvent | dict) -> MemoryEvent:
        if isinstance(event, dict):
            event = MemoryEvent.model_validate(event)
        with self._lock:
            mem = self._memories.setdefault(npc_id, NPCMemory(npc_id=npc_id))
            mem.events.append(event)
            self._maybe_summarise(mem)
        return event

    def record_many(self, npc_id: str, events: Iterable[MemoryEvent | dict]) -> None:
        for ev in events:
            self.record(npc_id, ev)

    def recent(self, npc_id: str, n: int = 10) -> list[MemoryEvent]:
        with self._lock:
            mem = self._memories.get(npc_id)
            if mem is None:
                return []
            non_facts = [e for e in mem.events if e.kind != "fact"]
            return non_facts[-n:]

    def facts(self, npc_id: str) -> list[MemoryEvent]:
        with self._lock:
            mem = self._memories.get(npc_id)
            if mem is None:
                return []
            return [e for e in mem.events if e.kind == "fact"]

    def summary(self, npc_id: str) -> str:
        with self._lock:
            mem = self._memories.get(npc_id)
            return mem.summary if mem else ""

    def known_npcs(self) -> list[str]:
        with self._lock:
            return list(self._memories.keys())

    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict:
        with self._lock:
            return {npc_id: mem.model_dump() for npc_id, mem in self._memories.items()}

    def restore(self, snapshot: dict) -> None:
        with self._lock:
            self._memories = {
                npc_id: NPCMemory.model_validate(payload)
                for npc_id, payload in (snapshot or {}).items()
            }

    # ------------------------------------------------------------------ #

    def _maybe_summarise(self, mem: NPCMemory) -> None:
        non_facts = [e for e in mem.events if e.kind != "fact"]
        if len(non_facts) <= self.max_events_per_npc:
            return
        overflow = len(non_facts) - self.max_events_per_npc
        # Pop oldest non-fact events FIFO.
        to_summarise: list[MemoryEvent] = []
        kept: list[MemoryEvent] = []
        removed = 0
        for ev in mem.events:
            if ev.kind != "fact" and removed < overflow:
                to_summarise.append(ev)
                removed += 1
            else:
                kept.append(ev)
        if self.summariser is not None and to_summarise:
            try:
                add = self.summariser(to_summarise)
                if add:
                    mem.summary = (mem.summary + " " + add).strip()
            except Exception:  # noqa: BLE001 — summariser is user-supplied
                pass
        else:
            # default: append a short bullet of dropped texts
            tail = "; ".join(e.text for e in to_summarise)
            mem.summary = (mem.summary + " | " + tail).strip(" |")
        mem.events = kept

