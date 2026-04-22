"""Bridge ``foundry.player_input`` events into the Director.

Subscribes to ``foundry.player_input`` on the in-process EventBus
(re-published by :class:`SocketBridge` from the ``{type:"event",
event:"player_input"}`` envelopes the Foundry chat hook sends), runs
the existing turn pipeline through :meth:`Director.handle_player_input`,
and pushes the resulting narration / dialogue back to Foundry as a
``narration`` event the module renders as a ``ChatMessage``.

The dispatcher serialises turns globally because the shared
:class:`PromptContextBuilder` only carries one ``character`` at a time
— we swap it per actor under a lock and restore it after the call so
overlapping turns from different PCs do not bleed into each other.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger("ai_dm.orchestration.player_input_dispatcher")


class PlayerInputDispatcher:
    EVENT_NAME = "foundry.player_input"

    def __init__(
        self,
        *,
        event_bus,
        sessions,
        client=None,
        prompt_context=None,
        director=None,
    ) -> None:
        self.event_bus = event_bus
        self.sessions = sessions
        self.client = client
        self.prompt_context = prompt_context
        self.director = director
        self._unsubscribe = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #

    def wire(self, director) -> None:
        """Attach the Director (built later than the dispatcher)."""
        self.director = director

    def start(self) -> None:
        if self._unsubscribe is not None:
            return
        self._unsubscribe = self.event_bus.subscribe(
            self.EVENT_NAME, self._on_player_input
        )
        logger.info("player_input dispatcher subscribed")

    def stop(self) -> None:
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            finally:
                self._unsubscribe = None

    # ------------------------------------------------------------------ #

    def _on_player_input(self, payload: dict[str, Any]) -> None:
        text = (payload.get("text") or "").strip()
        if not text:
            return
        actor_id = payload.get("actor_id")
        if not actor_id:
            logger.warning("player_input missing actor_id: %s", payload)
            return
        actor_name = payload.get("actor_name") or actor_id
        user_id = payload.get("user_id")
        user_name = payload.get("user_name")
        scene_id = payload.get("scene_id")

        if self.director is None:
            logger.warning("player_input received before Director was wired")
            return

        session = self.sessions.get_or_create(
            actor_id,
            actor_name,
            user_id=user_id,
            user_name=user_name,
        )
        if scene_id:
            session.scene_id = scene_id

        try:
            with self._lock:
                previous_character = None
                if self.prompt_context is not None and session.character_sheet:
                    previous_character = self.prompt_context.character
                    self.prompt_context.character = session.character_sheet
                try:
                    result = self.director.handle_player_input(
                        text, scene_id=scene_id
                    )
                finally:
                    if self.prompt_context is not None and session.character_sheet:
                        self.prompt_context.character = previous_character
        except Exception as exc:  # noqa: BLE001
            logger.exception("director failed for actor=%s: %s", actor_id, exc)
            self._push_narration(
                actor_id=actor_id,
                user_id=user_id,
                narration=f"[error: {exc}]",
                dialogue=[],
                commands_ok=False,
            )
            return

        session.turn_count += 1
        narration = (getattr(result, "narration", "") or "").strip()
        dialogue = [d.model_dump() for d in getattr(result, "dialogue", []) or []]
        commands_ok = bool(
            getattr(result, "metadata", {}).get("commands_ok", True)
        )
        self._push_narration(
            actor_id=actor_id,
            user_id=user_id,
            narration=narration,
            dialogue=dialogue,
            commands_ok=commands_ok,
        )

    def _push_narration(
        self,
        *,
        actor_id: str,
        user_id: str | None,
        narration: str,
        dialogue: list[dict],
        commands_ok: bool,
        whisper_to: str | None = None,
    ) -> None:
        if self.client is None:
            return
        envelope = {
            "actor_id": actor_id,
            "user_id": user_id,
            "narration": narration,
            "dialogue": dialogue,
            "commands_ok": commands_ok,
            "whisper_to": whisper_to,
        }
        try:
            self.client.send_event("narration", envelope)
        except Exception as exc:  # noqa: BLE001
            logger.warning("send_event(narration) failed: %s", exc)

