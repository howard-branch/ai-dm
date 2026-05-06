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

Threading
---------
``foundry.player_input`` is published from the Foundry client's
WebSocket receive loop. If we ran the Director synchronously in that
thread, every Foundry response envelope (``result``, ``who_ack``,
``event``) would be queued behind the still-running turn — which means
the *very* command the Director just dispatched (e.g. ``move_actor_to``)
could never complete because its own response cannot be delivered.

To break that re-entrant deadlock, the dispatcher hands the turn to a
single background worker thread. The recv loop returns immediately,
result envelopes flow as expected, and turns are still serialised
because the worker has a one-slot queue.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
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
        # Worker queue + thread. None means "run synchronously" (used
        # by tests that want deterministic ordering without spinning a
        # thread).
        self._work_queue: "queue.Queue[dict[str, Any] | None]" = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        # Buffer for events that arrive before ``wire(director)`` is
        # called (the container builds + subscribes before
        # ``bootstrap.build_runtime`` constructs the Director). Without
        # this, the very first ``/act`` typed during startup is silently
        # dropped with the "player_input received before Director was
        # wired" warning.
        self._pending_pre_wire: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ #

    def wire(self, director) -> None:
        """Attach the Director (built later than the dispatcher).

        Replays any events that arrived during the wire-up window so
        the player's first turn isn't dropped on the floor.
        """
        self.director = director
        if self._pending_pre_wire:
            buffered = self._pending_pre_wire
            self._pending_pre_wire = []
            logger.info(
                "wire(): replaying %d player_input event(s) buffered before Director",
                len(buffered),
            )
            for payload in buffered:
                try:
                    self._work_queue.put_nowait(payload)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("buffered replay enqueue failed: %s", exc)

    def start(self) -> None:
        if self._unsubscribe is not None:
            return
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="player-input-worker",
            daemon=True,
        )
        self._worker.start()
        self._unsubscribe = self.event_bus.subscribe(
            self.EVENT_NAME, self._enqueue_player_input
        )
        # Also surface mechanical failures (e.g. move target not found)
        # back to the player in chat — otherwise the LLM narration runs
        # as though the move succeeded and the player has no idea why
        # nothing happened on the canvas.
        self._unsub_move_failed = self.event_bus.subscribe(
            "intent.move_failed", self._on_move_failed
        )
        logger.info("player_input dispatcher subscribed (worker thread)")

    def stop(self) -> None:
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            finally:
                self._unsubscribe = None
        unsub_mf = getattr(self, "_unsub_move_failed", None)
        if unsub_mf is not None:
            try:
                unsub_mf()
            except Exception:  # noqa: BLE001
                pass
            self._unsub_move_failed = None
        self._stop.set()
        # Wake the worker so it can exit promptly.
        try:
            self._work_queue.put_nowait(None)
        except Exception:  # noqa: BLE001
            pass
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Block until the worker has drained the queue (test helper)."""
        import time as _t

        deadline = _t.monotonic() + timeout
        while _t.monotonic() < deadline:
            if self._work_queue.empty() and not self._lock.locked():
                # Tiny grace so the worker can finish its current
                # ``_on_player_input`` call after popping the last item.
                _t.sleep(0.02)
                if self._work_queue.empty() and not self._lock.locked():
                    return True
            _t.sleep(0.02)
        return False

    # ------------------------------------------------------------------ #

    def _enqueue_player_input(self, payload: dict[str, Any]) -> None:
        """Push the turn onto the worker queue and return immediately.

        Runs in the WebSocket receive thread. Must NOT block — anything
        that takes more than a few microseconds would starve inbound
        ``result`` envelopes (and deadlock the very command dispatched
        by the resulting Director turn).
        """
        # Pre-wire window: hold the event until ``wire(director)`` is
        # called. Without this the worker would dequeue it, see
        # ``self.director is None``, and drop the turn.
        if self.director is None:
            self._pending_pre_wire.append(payload)
            logger.info(
                "buffered player_input (Director not wired yet); will replay on wire()"
            )
            return
        try:
            self._work_queue.put_nowait(payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("player_input enqueue failed (dropped turn): %s", exc)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                payload = self._work_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if payload is None:
                return
            try:
                self._on_player_input(payload)
            except Exception:  # noqa: BLE001
                logger.exception("player_input worker raised; turn dropped")

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
        origin = payload.get("origin")

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

        turn_started = time.monotonic()
        logger.info(
            "TURN START actor=%s text=%r scene=%s", actor_id, text, scene_id,
        )
        try:
            with self._lock:
                previous_character = None
                if self.prompt_context is not None and session.character_sheet:
                    previous_character = self.prompt_context.character
                    self.prompt_context.character = session.character_sheet
                if self.prompt_context is not None:
                    party_names = [m.get("name") for m in (self.prompt_context.party or [])]
                    pc_name = (self.prompt_context.character or {}).get("name")
                    logger.info(
                        "act turn: actor=%s pc=%s party=%s text=%r scene=%s",
                        actor_id, pc_name, party_names, text, scene_id,
                    )
                try:
                    director_started = time.monotonic()
                    logger.info("TURN → director.handle_player_input()")
                    result = self.director.handle_player_input(
                        text, scene_id=scene_id, actor_id=actor_id,
                        origin=origin,
                    )
                    logger.info(
                        "TURN ← director returned in %.2fs",
                        time.monotonic() - director_started,
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
        logger.info(
            "TURN END actor=%s total=%.2fs commands_ok=%s",
            actor_id, time.monotonic() - turn_started, commands_ok,
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

    # ------------------------------------------------------------------ #

    def _on_move_failed(self, payload: dict[str, Any]) -> None:
        """Echo a mechanical move failure back to the player in chat.

        ``payload`` shape (from :class:`IntentRouter._dispatch_move`):
            ``{actor_id, target, target_id, scene_id, error}``

        ``error`` for the common "target not found" path now looks like
        ``"move_actor_to: target not found on scene X: overlook —
        available: gate, scriptorium, well"`` — perfect for surfacing
        verbatim so the player learns what targets *do* exist.
        """
        if self.client is None:
            return
        actor_id = payload.get("actor_id") or "unknown"
        target = payload.get("target") or payload.get("target_id") or "(no target)"
        err = (payload.get("error") or "move failed").strip()
        # Strip the verbose machine prefix so the chat message reads
        # naturally. Keep the "available:" tail — it's the actionable bit.
        clean = err
        for prefix in (
            "move_actor_to: target not found on scene ",
            "move_actor_to: ",
            "Foundry command failed: ",
        ):
            if clean.startswith(prefix):
                clean = clean[len(prefix):]
                break
        narration = f"You can't move to {target!r} from here — {clean}"
        envelope = {
            "actor_id": actor_id,
            "user_id": None,
            "narration": narration,
            "dialogue": [],
            "commands_ok": False,
            "whisper_to": None,
        }
        try:
            self.client.send_event("narration", envelope)
        except Exception as exc:  # noqa: BLE001
            logger.warning("send_event(move_failed narration) failed: %s", exc)

