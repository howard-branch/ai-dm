"""High-level speech-input facade.

Combines :class:`MicRecorder` (capture) and :class:`Transcriber`
(speech → text) so callers don't have to know which backend is active.

Two patterns:

* :meth:`listen_for` — record a fixed window then transcribe.
* :meth:`begin` — start recording, return a callable that stops and
  yields the transcript. Use this for push-to-talk REPL flows.

Both return ``""`` (and log a warning) if microphone or transcription
backends are unavailable, so the caller never has to special-case
"audio not configured" beyond checking the empty string.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from ai_dm.audio.mic_recorder import MicConfig, MicRecorder, MicUnavailable
from ai_dm.audio.transcribe import Transcriber, default_transcriber

logger = logging.getLogger("ai_dm.audio.input")


class SpeechInput:
    def __init__(
        self,
        *,
        recorder: MicRecorder | None = None,
        transcriber: Transcriber | None = None,
    ) -> None:
        self.recorder = recorder or MicRecorder()
        self.transcriber = transcriber or default_transcriber()

    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        return self.recorder.is_available() and self.transcriber.is_available()

    def status(self) -> dict:
        return {
            "mic_tool": self.recorder.tool_name(),
            "mic_available": self.recorder.is_available(),
            "transcribe_backend": self.transcriber.active_backend_name(),
            "transcribe_available": self.transcriber.is_available(),
        }

    # ------------------------------------------------------------------ #

    def listen_for(self, seconds: float = 5.0) -> str:
        """Record ``seconds`` of audio and return the transcript."""
        if not self.recorder.is_available():
            logger.warning("mic recorder unavailable")
            return ""
        try:
            wav = self.recorder.record_for(seconds)
        except MicUnavailable as exc:
            logger.warning("mic unavailable: %s", exc)
            return ""
        return self._transcribe_and_cleanup(wav)

    def begin(self) -> Callable[[], str]:
        """Start push-to-talk capture. Returns ``stop()`` → transcript."""
        if not self.recorder.is_available():
            logger.warning("mic recorder unavailable")
            return lambda: ""
        try:
            wav, stop_recording = self.recorder.record_until()
        except MicUnavailable as exc:
            logger.warning("mic unavailable: %s", exc)
            return lambda: ""

        def _stop() -> str:
            try:
                stop_recording()
            except Exception as exc:  # noqa: BLE001
                logger.warning("mic stop failed: %s", exc)
                return ""
            return self._transcribe_and_cleanup(wav)

        return _stop

    # Backwards-compat shim — older code calls ``listen()`` with no args.
    def listen(self, seconds: float = 5.0) -> str:
        return self.listen_for(seconds)

    # ------------------------------------------------------------------ #
    # Hands-free: record until silence, then transcribe.
    # ------------------------------------------------------------------ #

    def listen_vad(
        self,
        *,
        cancel: threading.Event | None = None,
        on_speech_start: Callable[[], None] | None = None,
    ) -> str:
        """Record until the speaker stops talking; return the transcript.

        Returns ``""`` if no speech was detected, the mic is unavailable
        or ``cancel`` fires before audio is captured.
        """
        if not self.recorder.is_available():
            logger.warning("mic recorder unavailable")
            return ""
        try:
            wav = self.recorder.record_with_vad(
                cancel=cancel,
                on_speech_start=on_speech_start,
            )
        except MicUnavailable as exc:
            logger.warning("mic unavailable: %s", exc)
            return ""
        return self._transcribe_and_cleanup(wav)

    # ------------------------------------------------------------------ #

    def _transcribe_and_cleanup(self, wav: Path) -> str:
        try:
            if not wav.exists() or wav.stat().st_size == 0:
                return ""
            return self.transcriber.transcribe(wav)
        finally:
            try:
                wav.unlink(missing_ok=True)  # type: ignore[call-arg]
            except Exception:  # noqa: BLE001
                pass


__all__ = ["SpeechInput", "MicConfig"]
