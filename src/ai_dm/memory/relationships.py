"""Directed relationship matrix between NPCs / players."""
from __future__ import annotations

import threading
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from ai_dm.utils.time import now_iso


def _clamp(v: int, lo: int = -100, hi: int = 100) -> int:
    return max(lo, min(hi, v))


class Relationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    target: str
    disposition: int = 0  # -100 (hate) .. +100 (love)
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    updated_at: str = Field(default_factory=now_iso)


class RelationshipMatrix:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._rels: dict[tuple[str, str], Relationship] = {}

    # ------------------------------------------------------------------ #

    def set(
        self,
        subject: str,
        target: str,
        disposition: int,
        *,
        tags: Iterable[str] | None = None,
        notes: str | None = None,
    ) -> Relationship:
        with self._lock:
            rel = self._rels.get((subject, target)) or Relationship(
                subject=subject, target=target
            )
            rel.disposition = _clamp(int(disposition))
            if tags is not None:
                rel.tags = list(tags)
            if notes is not None:
                rel.notes = notes
            rel.updated_at = now_iso()
            self._rels[(subject, target)] = rel
            return rel

    def adjust(
        self,
        subject: str,
        target: str,
        delta: int,
        *,
        tag: str | None = None,
    ) -> Relationship:
        with self._lock:
            rel = self._rels.get((subject, target)) or Relationship(
                subject=subject, target=target
            )
            rel.disposition = _clamp(rel.disposition + int(delta))
            if tag and tag not in rel.tags:
                rel.tags.append(tag)
            rel.updated_at = now_iso()
            self._rels[(subject, target)] = rel
            return rel

    def get(self, subject: str, target: str) -> Relationship | None:
        with self._lock:
            return self._rels.get((subject, target))

    def for_subject(self, subject: str) -> list[Relationship]:
        with self._lock:
            return [r for (s, _t), r in self._rels.items() if s == subject]

    def all(self) -> list[Relationship]:
        with self._lock:
            return list(self._rels.values())

    # ------------------------------------------------------------------ #

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [r.model_dump() for r in self._rels.values()]

    def restore(self, snapshot: list[dict] | None) -> None:
        with self._lock:
            self._rels = {}
            for entry in snapshot or []:
                rel = Relationship.model_validate(entry)
                self._rels[(rel.subject, rel.target)] = rel

