"""Per-actor session state for chat-driven multi-player play.

When a Foundry user types ``/act <text>``, the resulting envelope
carries a ``user_id`` *and* ``actor_id``. We key sessions on the actor
so two players sharing a single PC speak with one voice, and so the
narrator's prompt context can be swapped per turn without leaking
state between PCs.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("ai_dm.orchestration.actor_session")


@dataclass
class ActorSession:
    actor_id: str
    actor_name: str
    user_id: str | None = None
    user_name: str | None = None
    character_sheet: dict[str, Any] | None = None
    scene_id: str | None = None
    last_seen: float = field(default_factory=time.time)
    turn_count: int = 0


class ActorSessionRegistry:
    """Lazily-built registry of per-actor sessions.

    The pack is consulted to load a character sheet for the actor. The
    resolution chain is:

    1. ``pack.state.characters / "<actor_id>.json"`` — live, mutable sheet.
    2. ``pack.paths.characters_seed / "<actor_id>.json"`` — pack seed.
    3. A minimal stub ``{"id": actor_id, "name": actor_name}``.
    """

    def __init__(self, pack: Optional[Any] = None) -> None:
        self.pack = pack
        self._lock = threading.Lock()
        self._sessions: dict[str, ActorSession] = {}

    def get_or_create(
        self,
        actor_id: str,
        actor_name: str | None = None,
        *,
        user_id: str | None = None,
        user_name: str | None = None,
    ) -> ActorSession:
        with self._lock:
            session = self._sessions.get(actor_id)
            if session is None:
                sheet = self._load_sheet(actor_id, actor_name or actor_id)
                session = ActorSession(
                    actor_id=actor_id,
                    actor_name=actor_name or (sheet.get("name") if sheet else actor_id),
                    user_id=user_id,
                    user_name=user_name,
                    character_sheet=sheet,
                )
                self._sessions[actor_id] = session
            else:
                if actor_name and not session.actor_name:
                    session.actor_name = actor_name
                if user_id:
                    session.user_id = user_id
                if user_name:
                    session.user_name = user_name
            session.last_seen = time.time()
            return session

    def get(self, actor_id: str) -> ActorSession | None:
        return self._sessions.get(actor_id)

    def all(self) -> list[ActorSession]:
        return list(self._sessions.values())

    # ------------------------------------------------------------------ #

    def _load_sheet(self, actor_id: str, actor_name: str) -> dict[str, Any]:
        pack = self.pack
        if pack is not None:
            for path in (
                pack.state.characters / f"{actor_id}.json",
                pack.paths.characters_seed / f"{actor_id}.json",
            ):
                try:
                    if path.exists():
                        return json.loads(path.read_text(encoding="utf-8"))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("character sheet %s unreadable: %s", path, exc)
        return {"id": actor_id, "name": actor_name}

