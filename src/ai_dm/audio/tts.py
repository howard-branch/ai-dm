"""Text-to-speech backends.

The default ``EdgeTTSBackend`` uses the ``edge-tts`` Python package to call
Microsoft Edge's online TTS service. ``PiperBackend`` shells out to a
local ``piper`` binary for offline synthesis. ``NullBackend`` returns
empty bytes (used in tests).

If both edge-tts (network) and piper (binary) are unavailable, the audio
queue silently drops items rather than crashing the game loop.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("ai_dm.audio.tts")


class TTSBackend(Protocol):
    name: str

    def synthesize(self, text: str, voice: str | None = None) -> bytes: ...

    def is_available(self) -> bool: ...


@dataclass
class NullBackend:
    name: str = "null"
    calls: list[tuple[str, str | None]] | None = None

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        self.calls.append((text, voice))  # type: ignore[union-attr]
        return b""

    def is_available(self) -> bool:
        return True


class EdgeTTSBackend:
    """edge-tts (Microsoft Edge online voices). Returns mp3 bytes."""

    name = "edge-tts"

    def __init__(self, *, default_voice: str = "en-GB-SoniaNeural") -> None:
        self.default_voice = default_voice
        self._available: bool | None = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import edge_tts  # noqa: F401  type: ignore[import-not-found]
            self._available = True
        except Exception:  # noqa: BLE001
            self._available = False
        return self._available

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        if not text.strip():
            return b""
        if not self.is_available():
            raise RuntimeError("edge-tts not installed")
        import asyncio

        import edge_tts  # type: ignore[import-not-found]

        async def _run() -> bytes:
            communicate = edge_tts.Communicate(text, voice or self.default_voice)
            chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    chunks.append(chunk.get("data") or b"")
            return b"".join(chunks)

        try:
            return asyncio.run(_run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_run())
            finally:
                loop.close()

    # ------------------------------------------------------------------ #
    # Streaming path — yields MP3 chunks as edge-tts produces them, so a
    # downstream player can begin decoding the first frames within a few
    # hundred ms instead of waiting for the full clip.
    # ------------------------------------------------------------------ #
    def stream(self, text: str, voice: str | None = None):
        """Yield MP3 chunks as they arrive from edge-tts.

        Implemented by running the async stream on a private event loop
        in a daemon thread, and pulling chunks through a thread-safe
        queue. The generator is exhausted when the upstream stream
        completes (or raises).
        """
        if not text.strip():
            return
        if not self.is_available():
            raise RuntimeError("edge-tts not installed")

        import asyncio
        import queue
        import threading

        import edge_tts  # type: ignore[import-not-found]

        q: "queue.Queue[bytes | None | BaseException]" = queue.Queue(maxsize=64)
        sentinel = None
        chosen_voice = voice or self.default_voice

        async def _producer() -> None:
            try:
                communicate = edge_tts.Communicate(text, chosen_voice)
                async for chunk in communicate.stream():
                    if chunk.get("type") == "audio":
                        data = chunk.get("data") or b""
                        if data:
                            q.put(data)
            except BaseException as exc:  # noqa: BLE001
                q.put(exc)
            finally:
                q.put(sentinel)

        def _run() -> None:
            asyncio.run(_producer())

        threading.Thread(target=_run, name="edge-tts-stream", daemon=True).start()

        while True:
            item = q.get()
            if item is sentinel:
                return
            if isinstance(item, BaseException):
                raise item
            yield item


class PiperBackend:
    """Offline TTS via the piper binary (https://github.com/rhasspy/piper)."""

    name = "piper"

    def __init__(
        self,
        *,
        binary: str = "piper",
        model_path: Path | None = None,
    ) -> None:
        self.binary = binary
        self.model_path = Path(model_path) if model_path else None
        self._available: bool | None = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        self._available = shutil.which(self.binary) is not None and (
            self.model_path is None or self.model_path.exists()
        )
        return self._available

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        if not text.strip():
            return b""
        if not self.is_available():
            raise RuntimeError("piper backend unavailable")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            out_path = Path(tmp.name)
        cmd = [self.binary, "--output_file", str(out_path)]
        if self.model_path is not None:
            cmd += ["--model", str(self.model_path)]
        try:
            subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            return out_path.read_bytes()
        finally:
            try:
                out_path.unlink(missing_ok=True)  # type: ignore[call-arg]
            except Exception:  # noqa: BLE001
                pass


class FallbackTTSBackend:
    """Try a primary backend; on failure (or unavailability) use a fallback.

    This is the default plumbing for Phase 3: edge-tts → piper → null.
    """

    name = "fallback"

    def __init__(self, primary: TTSBackend, fallback: TTSBackend) -> None:
        self.primary = primary
        self.fallback = fallback

    def is_available(self) -> bool:
        return self.primary.is_available() or self.fallback.is_available()

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        if self.primary.is_available():
            try:
                return self.primary.synthesize(text, voice)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "TTS primary %r failed (%s) — falling back to %r",
                    self.primary.name, exc, self.fallback.name,
                )
        if self.fallback.is_available():
            try:
                return self.fallback.synthesize(text, voice)
            except Exception as exc:  # noqa: BLE001
                logger.warning("TTS fallback %r also failed: %s", self.fallback.name, exc)
        return b""


def default_backend(*, edge_voice: str = "en-GB-SoniaNeural") -> TTSBackend:
    """Construct the standard edge-tts → piper → null chain."""
    return FallbackTTSBackend(
        primary=EdgeTTSBackend(default_voice=edge_voice),
        fallback=FallbackTTSBackend(primary=PiperBackend(), fallback=NullBackend()),
    )


class TTSService:
    """Backwards-compatible synchronous wrapper."""

    def __init__(self, backend: TTSBackend | None = None) -> None:
        self.backend = backend or default_backend()
        self._log = logging.getLogger("ai_dm.audio.tts.service")

    def speak(self, text: str, voice: str | None = None) -> bytes:
        try:
            return self.backend.synthesize(text, voice)
        except Exception as exc:  # noqa: BLE001
            self._log.warning("TTS speak failed: %s", exc)
            return b""
