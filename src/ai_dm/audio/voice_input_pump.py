"""Host-side voice input pump for Foundry-driven mode.

Replaces the old ``Runtime._voice_loop`` REPL helper. Runs in a daemon
thread, captures mic audio with :class:`SpeechInput.listen_vad`, and
publishes each transcript as a synthetic ``foundry.player_input`` event
on the in-process :class:`EventBus`. The existing
``PlayerInputDispatcher`` then runs the turn exactly as it does for an
``/act`` chat message — including pushing the narration back to the
Foundry browser.

Lifecycle is controlled at runtime by the ``foundry.voice_control``
event (see :class:`VoiceControlBridge`) so a player can toggle voice
from the Foundry browser via ``/voice on|off|toggle``.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Any, Optional

logger = logging.getLogger("ai_dm.audio.voice_pump")

# Re-used from the legacy REPL voice loop.
_VOICE_EXIT_PHRASES = (
    "stop listening", "stop voice", "end voice", "exit voice",
    "exit voice mode", "leave voice mode", "quit voice", "voice off",
)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text or "")).strip().lower()


class VoiceInputPump:
    """Background mic → transcript → ``foundry.player_input`` pump."""

    EVENT_NAME = "foundry.player_input"

    def __init__(
        self,
        *,
        event_bus,
        speech_input,
        audio_queue=None,
        client=None,
        actor_id: str | None = None,
        actor_name: str | None = None,
        user_id: str | None = None,
        scene_id: str | None = None,
    ) -> None:
        self.event_bus = event_bus
        self.speech_input = speech_input
        self.audio_queue = audio_queue
        self.client = client
        self.actor_id = actor_id
        self.actor_name = actor_name
        self.user_id = user_id
        self.scene_id = scene_id

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # Cancels an in-flight ffmpeg recording when stop() is called.
        self._cancel_record = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_available(self) -> bool:
        si = self.speech_input
        if si is None:
            return False
        try:
            return bool(si.is_available())
        except Exception:  # noqa: BLE001
            return False

    def availability_reason(self) -> str:
        si = self.speech_input
        if si is None:
            return "speech input not configured"
        try:
            status = si.status()
        except Exception as exc:  # noqa: BLE001
            return f"speech input error: {exc}"
        if not status.get("mic_available"):
            return "no microphone tool found (install ffmpeg, arecord or parec)"
        if not status.get("transcribe_available"):
            return "no speech-to-text backend (set OPENAI_API_KEY or install faster-whisper)"
        return "ok"

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def configure(
        self,
        *,
        actor_id: str | None = None,
        actor_name: str | None = None,
        user_id: str | None = None,
        scene_id: str | None = None,
    ) -> None:
        """Update which actor to attribute transcripts to. Hot-swappable."""
        with self._lock:
            if actor_id is not None:
                self.actor_id = actor_id
            if actor_name is not None:
                self.actor_name = actor_name
            if user_id is not None:
                self.user_id = user_id
            if scene_id is not None:
                self.scene_id = scene_id

    def start(self) -> bool:
        """Start the pump. Returns True if it actually started."""
        with self._lock:
            if self.is_running:
                return False
            if not self.is_available():
                logger.warning(
                    "voice pump cannot start: %s", self.availability_reason()
                )
                return False
            if not self.actor_id:
                logger.warning(
                    "voice pump cannot start: no actor_id configured"
                )
                return False
            self._stop.clear()
            self._cancel_record.clear()
            self._thread = threading.Thread(
                target=self._loop, name="voice-input-pump", daemon=True
            )
            self._thread.start()
            logger.info(
                "voice pump started (actor=%s, scene=%s)",
                self.actor_id, self.scene_id,
            )
            return True

    def stop(self, *, join_timeout: float = 2.0) -> bool:
        """Stop the pump. Returns True if it was running."""
        with self._lock:
            t = self._thread
            if t is None:
                return False
            self._stop.set()
            self._cancel_record.set()
            self._thread = None
        try:
            t.join(timeout=join_timeout)
        except Exception:  # noqa: BLE001
            pass
        logger.info("voice pump stopped")
        return True

    def toggle(self) -> bool:
        """Flip state. Returns the new ``is_running`` value."""
        if self.is_running:
            self.stop()
            return False
        return self.start()

    # ------------------------------------------------------------------ #
    # Loop
    # ------------------------------------------------------------------ #

    def _loop(self) -> None:
        speech = self.speech_input
        try:
            while not self._stop.is_set():
                # Wait for any DM narration to finish playing so we
                # don't transcribe our own TTS.
                if self.audio_queue is not None:
                    try:
                        self.audio_queue.join(timeout=60.0)
                    except Exception:  # noqa: BLE001
                        pass
                if self._stop.is_set():
                    break

                self._cancel_record.clear()
                try:
                    text = speech.listen_vad(cancel=self._cancel_record)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("voice listen_vad failed: %s", exc)
                    # Brief backoff so a misconfigured mic doesn't spin.
                    if self._stop.wait(timeout=1.0):
                        break
                    continue

                text = (text or "").strip()
                if not text:
                    continue

                norm = _normalise(text)
                if any(p in norm for p in _VOICE_EXIT_PHRASES):
                    logger.info("voice pump heard exit phrase — stopping")
                    self._stop.set()
                    break

                self._publish(text)
        finally:
            # Make sure no stale handle is left if the loop exited on its own.
            self._cancel_record.set()

    def _publish(self, text: str) -> None:
        with self._lock:
            payload: dict[str, Any] = {
                "actor_id": self.actor_id,
                "actor_name": self.actor_name,
                "user_id": self.user_id,
                "scene_id": self.scene_id,
                "text": text,
                "source": "voice",
            }
        try:
            self.event_bus.publish(self.EVENT_NAME, payload)
            logger.info("voice → player_input: %r", text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("voice publish failed: %s", exc)


# --------------------------------------------------------------------- #
# Foundry control bridge
# --------------------------------------------------------------------- #


class VoiceControlBridge:
    """Subscribe to ``foundry.voice_control`` events and drive the pump.

    Payload shape (from the Foundry ``/voice`` chat command):
        {"action": "on"|"off"|"toggle"|"status",
         "actor_id": str, "actor_name": str | None,
         "user_id": str | None, "scene_id": str | None}
    """

    EVENT_NAME = "foundry.voice_control"

    def __init__(self, *, event_bus, pump: VoiceInputPump, client=None) -> None:
        self.event_bus = event_bus
        self.pump = pump
        self.client = client
        self._unsubscribe = None

    def start(self) -> None:
        if self._unsubscribe is not None:
            return
        self._unsubscribe = self.event_bus.subscribe(
            self.EVENT_NAME, self._on_voice_control
        )

    def stop(self) -> None:
        if self._unsubscribe is None:
            return
        try:
            self._unsubscribe()
        finally:
            self._unsubscribe = None

    # ------------------------------------------------------------------ #

    def _on_voice_control(self, payload: dict[str, Any]) -> None:
        action = str(payload.get("action") or "").strip().lower()
        # Always allow Foundry to (re)bind the actor — the pump was
        # constructed before the player had picked a token.
        self.pump.configure(
            actor_id=payload.get("actor_id"),
            actor_name=payload.get("actor_name"),
            user_id=payload.get("user_id"),
            scene_id=payload.get("scene_id"),
        )

        if action in ("on", "start", "enable"):
            ok = self.pump.start()
            self._notify(payload, "on" if ok else "error",
                        ok=ok, reason=None if ok else self.pump.availability_reason())
        elif action in ("off", "stop", "disable"):
            self.pump.stop()
            self._notify(payload, "off", ok=True)
        elif action == "toggle":
            new_state = self.pump.toggle()
            self._notify(payload, "on" if new_state else "off", ok=True)
        elif action in ("status", "", "info"):
            self._notify(
                payload,
                "on" if self.pump.is_running else "off",
                ok=True,
                reason=self.pump.availability_reason(),
            )
        else:
            logger.warning("voice_control: unknown action %r", action)
            self._notify(payload, "error", ok=False, reason=f"unknown action {action!r}")

    def _notify(
        self,
        payload: dict[str, Any],
        state: str,
        *,
        ok: bool,
        reason: Optional[str] = None,
    ) -> None:
        if self.client is None:
            return
        msg = f"[voice {state}]" + (f" — {reason}" if reason and not ok else "")
        try:
            self.client.send_event("narration", {
                "actor_id": payload.get("actor_id"),
                "user_id": payload.get("user_id"),
                "narration": msg,
                "dialogue": [],
                "commands_ok": ok,
                "whisper_to": payload.get("user_id"),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("voice_control feedback failed: %s", exc)

