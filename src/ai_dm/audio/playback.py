"""Cross-platform audio playback helper.

We avoid pulling in heavy Python audio packages (simpleaudio, sounddevice,
PyAudio) and instead pipe bytes to whatever common system player is
available. Detection is cached.

Two entry points:

* :func:`play_bytes` — blocking; play a complete in-memory clip.
* :func:`play_stream` — blocking; open the player once and stream chunks
  into its stdin as they arrive. Use this when synthesising via a
  streaming TTS (e.g. edge-tts) to minimise time-to-first-sound.

Both return ``True`` on success, ``False`` if no player is available
or the process failed. Neither raises.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Iterable, Iterator

logger = logging.getLogger("ai_dm.audio.playback")

# (binary, args) — all read audio from stdin; format is autodetected.
_PLAYERS: list[tuple[str, list[str]]] = [
    ("ffplay",  ["-nodisp", "-autoexit", "-loglevel", "quiet", "-i", "pipe:0"]),
    ("mpv",     ["--really-quiet", "--no-video", "-"]),
    ("mpg123",  ["-q", "-"]),
    ("paplay",  ["--raw=false"]),
    ("aplay",   ["-q", "-"]),
]

_resolved: tuple[str, list[str]] | None = None
_searched = False


def _find_player() -> tuple[str, list[str]] | None:
    global _resolved, _searched
    if _searched:
        return _resolved
    _searched = True
    for binary, args in _PLAYERS:
        if shutil.which(binary):
            _resolved = (binary, args)
            logger.info("audio playback using %s", binary)
            return _resolved
    logger.warning(
        "no audio player found on PATH; install one of: %s",
        ", ".join(b for b, _ in _PLAYERS),
    )
    _resolved = None
    return None


def play_bytes(audio: bytes) -> bool:
    """Play raw audio bytes (typically MP3 from edge-tts). Blocking."""
    if not audio:
        return False
    player = _find_player()
    if player is None:
        return False
    binary, args = player
    try:
        proc = subprocess.run(
            [binary, *args],
            input=audio,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
        return proc.returncode == 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("audio playback via %s failed: %s", binary, exc)
        return False


def play_stream(chunks: Iterator[bytes] | Iterable[bytes]) -> bool:
    """Stream chunks into the player's stdin as they arrive.

    Yields control to the player immediately so it can start decoding
    the first frames while later chunks are still being produced
    (downloaded, in the case of edge-tts). This dramatically reduces
    the perceived "time to first sound" for long narrations.
    """
    player = _find_player()
    if player is None:
        return False
    binary, args = player
    proc: subprocess.Popen | None = None
    wrote_anything = False
    try:
        proc = subprocess.Popen(
            [binary, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert proc.stdin is not None
        for chunk in chunks:
            if not chunk:
                continue
            try:
                proc.stdin.write(chunk)
                proc.stdin.flush()
                wrote_anything = True
            except BrokenPipeError:
                # Player exited (e.g. user killed it). Stop sending.
                break
        try:
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        proc.wait(timeout=120)
        return wrote_anything and proc.returncode == 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("audio streaming via %s failed: %s", binary, exc)
        if proc is not None:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        return False


def available_players() -> Iterable[str]:
    """For diagnostics: which players are on PATH."""
    return [b for b, _ in _PLAYERS if shutil.which(b)]

