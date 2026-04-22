"""
In-memory registry of Foundry scene/actor/token IDs.

The AI is allowed to refer to entities by **friendly name** or **alias**.
The registry maps those names back to the real Foundry document ids that
must be sent over the wire. Tokens are scoped per scene because the same
alias can legitimately collide across scenes.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Literal

from ai_dm.foundry.errors import RegistryMissError

EntityKind = Literal["scene", "actor", "token"]
_KINDS: tuple[EntityKind, ...] = ("scene", "actor", "token")


@dataclass
class RegistryEntry:
    kind: EntityKind
    foundry_id: str
    name: str
    aliases: set[str] = field(default_factory=set)
    scene_id: str | None = None  # only meaningful for tokens
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _normalise(key: str) -> str:
    return key.strip().lower()


class FoundryRegistry:
    """Thread-safe registry mapping aliases → Foundry IDs."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[EntityKind, dict[str, RegistryEntry]] = {k: {} for k in _KINDS}
        # Alias index. For scenes/actors: dict[alias_norm] -> foundry_id.
        # For tokens: dict[(scene_id_or_None, alias_norm)] -> foundry_id.
        self._scene_alias: dict[str, str] = {}
        self._actor_alias: dict[str, str] = {}
        self._token_alias: dict[tuple[str | None, str], str] = {}

    # ------------------------------------------------------------------ #
    # Mutators
    # ------------------------------------------------------------------ #

    def register(
        self,
        kind: EntityKind,
        foundry_id: str,
        *,
        name: str,
        aliases: Iterable[str] = (),
        scene_id: str | None = None,
    ) -> RegistryEntry:
        if kind not in _KINDS:
            raise ValueError(f"unknown kind: {kind}")
        if not foundry_id:
            raise ValueError("foundry_id must be non-empty")
        if not name:
            raise ValueError("name must be non-empty")
        if kind == "token" and not scene_id:
            raise ValueError("token registrations require a scene_id")

        all_aliases = {name, foundry_id, *aliases}
        with self._lock:
            entry = self._entries[kind].get(foundry_id)
            if entry is None:
                entry = RegistryEntry(
                    kind=kind,
                    foundry_id=foundry_id,
                    name=name,
                    aliases=set(),
                    scene_id=scene_id,
                )
                self._entries[kind][foundry_id] = entry
            else:
                # Update name/scene if the caller is correcting metadata.
                entry.name = name
                if scene_id is not None:
                    entry.scene_id = scene_id

            for alias in all_aliases:
                self._add_alias_locked(kind, foundry_id, alias, scene_id=entry.scene_id)
                entry.aliases.add(alias)

            return entry

    def add_alias(
        self,
        kind: EntityKind,
        foundry_id: str,
        alias: str,
    ) -> None:
        with self._lock:
            entry = self._entries[kind].get(foundry_id)
            if entry is None:
                raise RegistryMissError(kind, foundry_id)
            self._add_alias_locked(kind, foundry_id, alias, scene_id=entry.scene_id)
            entry.aliases.add(alias)

    def unregister(self, kind: EntityKind, foundry_id: str) -> RegistryEntry | None:
        with self._lock:
            entry = self._entries[kind].pop(foundry_id, None)
            if entry is None:
                return None
            for alias in entry.aliases:
                self._remove_alias_locked(kind, alias, scene_id=entry.scene_id)
            return entry

    # ------------------------------------------------------------------ #
    # Lookups
    # ------------------------------------------------------------------ #

    def resolve(
        self,
        kind: EntityKind,
        key: str,
        *,
        scene_id: str | None = None,
    ) -> str:
        if not key:
            raise RegistryMissError(kind, key, scene_id)

        with self._lock:
            # Direct id hit.
            if key in self._entries[kind]:
                return key

            norm = _normalise(key)
            if kind == "scene":
                hit = self._scene_alias.get(norm)
            elif kind == "actor":
                hit = self._actor_alias.get(norm)
            else:
                hit = None
                if scene_id is not None:
                    hit = self._token_alias.get((scene_id, norm))
                if hit is None:
                    # Fall back to a global lookup if exactly one token claims
                    # the alias across all scenes.
                    matches = [
                        fid
                        for (sid, alias), fid in self._token_alias.items()
                        if alias == norm
                    ]
                    if len(matches) == 1:
                        hit = matches[0]

            if hit is None:
                raise RegistryMissError(kind, key, scene_id)
            return hit

    def get(self, kind: EntityKind, foundry_id: str) -> RegistryEntry | None:
        with self._lock:
            return self._entries[kind].get(foundry_id)

    def all(self, kind: EntityKind) -> list[RegistryEntry]:
        with self._lock:
            return list(self._entries[kind].values())

    def snapshot(self) -> dict:
        with self._lock:
            return {
                kind: [
                    {
                        "foundry_id": e.foundry_id,
                        "name": e.name,
                        "aliases": sorted(e.aliases),
                        "scene_id": e.scene_id,
                        "created_at": e.created_at.isoformat(),
                    }
                    for e in self._entries[kind].values()
                ]
                for kind in _KINDS
            }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _add_alias_locked(
        self,
        kind: EntityKind,
        foundry_id: str,
        alias: str,
        *,
        scene_id: str | None,
    ) -> None:
        norm = _normalise(alias)
        if not norm:
            return
        if kind == "scene":
            existing = self._scene_alias.get(norm)
            if existing and existing != foundry_id:
                raise ValueError(f"alias {alias!r} already maps to scene {existing}")
            self._scene_alias[norm] = foundry_id
        elif kind == "actor":
            existing = self._actor_alias.get(norm)
            if existing and existing != foundry_id:
                raise ValueError(f"alias {alias!r} already maps to actor {existing}")
            self._actor_alias[norm] = foundry_id
        else:
            key = (scene_id, norm)
            existing = self._token_alias.get(key)
            if existing and existing != foundry_id:
                raise ValueError(
                    f"alias {alias!r} already maps to token {existing} in scene {scene_id}"
                )
            self._token_alias[key] = foundry_id

    def _remove_alias_locked(
        self,
        kind: EntityKind,
        alias: str,
        *,
        scene_id: str | None,
    ) -> None:
        norm = _normalise(alias)
        if kind == "scene":
            self._scene_alias.pop(norm, None)
        elif kind == "actor":
            self._actor_alias.pop(norm, None)
        else:
            self._token_alias.pop((scene_id, norm), None)

