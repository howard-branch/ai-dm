"""Cross-platform audio playback helper.

We avoid pulling in heavy Python audio packages (simpleaudio, sounddevice,
PyAudio) and instead pipe bytes to whatever common system player is
available. Detection is cached.

Returns ``True`` on success, ``False`` if no player is available or the
process failed. Never raises.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Iterable

logger = logging.getLogger("ai_dm.audio.playback")

# (binary, args-template) — args use {fmt} placeholder for the codec hint.
# All commands read the audio from stdin so we don't need temp files.
_PLAYERS: list[tuple[str, list[str]]] = [
    ("ffplay",  ["-nodisp", "-autoexit", "-loglevel", "quiet", "-i", "pipe:0"]),
    ("mpv",     ["--really-quiet", "--no-video", "-"]),
    ("mpg123",  ["-q", "-"]),
    ("paplay",  ["--raw=false"]),  # autodetects format from headers
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


def available_players() -> Iterable[str]:
    """For diagnostics: which players are on PATH."""
    return [b for b, _ in _PLAYERS if shutil.which(b)]

