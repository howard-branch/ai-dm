"""Microphone recorder using whichever system tool is on PATH.

We deliberately avoid Python audio packages (PyAudio, sounddevice) so
the runtime stays installable on a fresh box without a build toolchain
or PortAudio headers. Instead we shell out to a recorder binary and
read its WAV output from a temp file.

Detection order:

1. ``ffmpeg`` — most portable; works on Linux (ALSA / PulseAudio /
   PipeWire), macOS (avfoundation) and Windows (dshow).
2. ``arecord`` — ALSA (Linux only).
3. ``parec`` — PulseAudio (Linux only); we wrap its raw output into
   a WAV header.

Two recording modes:

* :meth:`record_for` — fixed-duration capture.
* :meth:`record_until` — push-to-talk: start recording, return a
  ``stop()`` callable, and finalise on call.

Both yield a path to a 16-kHz mono 16-bit PCM WAV — the format that
all of OpenAI Whisper, faster-whisper and vosk accept directly.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import signal
import struct
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger("ai_dm.audio.mic")


@dataclass
class MicConfig:
    sample_rate: int = 16000
    channels: int = 1
    # Linux input device hint for ffmpeg / arecord. ``"default"`` works
    # for both ALSA and PulseAudio in most installs.
    device: str = "default"
    # macOS: avfoundation device index (``"0"`` is usually the built-in
    # mic). Can be overridden via the ``AI_DM_MIC_DEVICE`` env var.
    macos_device: str = "0"
    # ---- VAD (silence-based auto-stop) ---------------------------- #
    # Threshold below which audio is considered silence (ffmpeg
    # silencedetect noise level, dBFS). -35 dB is a sensible default
    # for a quiet room with a typical headset / desk mic.
    vad_noise_db: float = -35.0
    # How many seconds of continuous silence end the recording.
    vad_silence_secs: float = 1.2
    # Hard ceiling so a stuck recording can't run forever.
    vad_max_secs: float = 45.0
    # If we never hear speech within this window, give up and return
    # an empty WAV — the caller treats it as "nothing said".
    vad_initial_grace_secs: float = 6.0


class MicUnavailable(RuntimeError):
    pass


class MicRecorder:
    def __init__(self, config: MicConfig | None = None) -> None:
        self.config = config or MicConfig()
        env_dev = os.environ.get("AI_DM_MIC_DEVICE")
        if env_dev:
            self.config.device = env_dev
            self.config.macos_device = env_dev
        self._tool = self._detect_tool()

    # ------------------------------------------------------------------ #

    @staticmethod
    def _detect_tool() -> str | None:
        for binary in ("ffmpeg", "arecord", "parec"):
            if shutil.which(binary):
                return binary
        return None

    def is_available(self) -> bool:
        return self._tool is not None

    def tool_name(self) -> str | None:
        return self._tool

    # ------------------------------------------------------------------ #
    # Fixed-duration capture
    # ------------------------------------------------------------------ #

    def record_for(self, seconds: float) -> Path:
        """Record exactly ``seconds`` of audio. Blocking."""
        if not self.is_available():
            raise MicUnavailable(
                "no microphone tool found; install ffmpeg, arecord or parec"
            )
        out = Path(tempfile.mkstemp(suffix=".wav", prefix="ai_dm_mic_")[1])
        cmd = self._build_cmd(out, max_seconds=seconds)
        try:
            subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=seconds + 5.0,
                check=False,
            )
        except subprocess.TimeoutExpired:
            pass
        if self._tool == "parec":
            self._wrap_parec_raw(out)
        return out

    # ------------------------------------------------------------------ #
    # Push-to-talk capture
    # ------------------------------------------------------------------ #

    def record_until(self) -> tuple[Path, Callable[[], Path]]:
        """Start recording in the background.

        Returns ``(out_path, stop)``. Call ``stop()`` to finalise and
        get back the same ``out_path`` once the recorder has flushed.
        """
        if not self.is_available():
            raise MicUnavailable(
                "no microphone tool found; install ffmpeg, arecord or parec"
            )
        out = Path(tempfile.mkstemp(suffix=".wav", prefix="ai_dm_mic_")[1])
        # Cap to 5 minutes as a safety net.
        cmd = self._build_cmd(out, max_seconds=300.0)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        stopped = threading.Event()

        def stop() -> Path:
            if stopped.is_set():
                return out
            stopped.set()
            # Politely ask the recorder to finish writing the WAV.
            try:
                if self._tool == "ffmpeg":
                    # ffmpeg responds to 'q' on stdin or SIGINT; SIGINT
                    # is more reliable when stdin is /dev/null.
                    proc.send_signal(signal.SIGINT)
                else:
                    proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
            # Tiny grace period so the OS flushes the file.
            time.sleep(0.05)
            if self._tool == "parec":
                self._wrap_parec_raw(out)
            return out

        return out, stop

    # ------------------------------------------------------------------ #
    # VAD (auto-stop on silence)
    # ------------------------------------------------------------------ #

    def record_with_vad(
        self,
        *,
        cancel: "threading.Event | None" = None,
        on_speech_start: Callable[[], None] | None = None,
    ) -> Path:
        """Record until the speaker stops talking (or ``cancel`` is set).

        Implementation: spawns ffmpeg with the ``silencedetect`` audio
        filter, parses its stderr in real time, and signals it to stop
        as soon as silence resumes after speech has been detected.
        Falls back to a fixed window if ffmpeg is not the active tool.

        Returns the path to a WAV file (possibly empty if no speech was
        detected within the grace period or if cancelled).
        """
        if not self.is_available():
            raise MicUnavailable(
                "no microphone tool found; install ffmpeg, arecord or parec"
            )

        if self._tool != "ffmpeg":
            # Best-effort fallback: capture vad_max_secs and let the
            # transcriber drop empty audio. Good enough for arecord.
            return self.record_for(min(8.0, self.config.vad_max_secs))

        out = Path(tempfile.mkstemp(suffix=".wav", prefix="ai_dm_vad_")[1])
        cmd = self._build_vad_cmd(out)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        speech_started = threading.Event()
        finished = threading.Event()
        start_ts = time.monotonic()

        def _stop_recorder() -> None:
            if finished.is_set():
                return
            finished.set()
            try:
                proc.send_signal(signal.SIGINT)
            except Exception:  # noqa: BLE001
                pass

        def _read_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                if finished.is_set():
                    break
                # silencedetect emits two interesting lines:
                #   silence_end: 1.234 | silence_duration: ...
                #   silence_start: 5.678
                if "silence_end" in line:
                    if not speech_started.is_set():
                        speech_started.set()
                        if on_speech_start is not None:
                            try:
                                on_speech_start()
                            except Exception:  # noqa: BLE001
                                pass
                elif "silence_start" in line:
                    if speech_started.is_set():
                        _stop_recorder()
                        return

        reader = threading.Thread(target=_read_stderr, name="ffmpeg-vad-reader", daemon=True)
        reader.start()

        # Supervisor loop: enforce grace + max-duration + cancel.
        try:
            while not finished.is_set():
                if proc.poll() is not None:
                    break
                if cancel is not None and cancel.is_set():
                    _stop_recorder()
                    break
                elapsed = time.monotonic() - start_ts
                if elapsed >= self.config.vad_max_secs:
                    _stop_recorder()
                    break
                if (
                    not speech_started.is_set()
                    and elapsed >= self.config.vad_initial_grace_secs
                ):
                    _stop_recorder()
                    break
                time.sleep(0.05)
        finally:
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
            reader.join(timeout=1.0)

        # If we never heard speech, blank the file so the caller's
        # "is empty?" check trivially returns True.
        if not speech_started.is_set():
            try:
                out.write_bytes(b"")
            except Exception:  # noqa: BLE001
                pass

        # Brief pause so the OS flushes the final samples to disk.
        time.sleep(0.05)
        return out

    def _build_vad_cmd(self, out: Path) -> list[str]:
        sr = str(self.config.sample_rate)
        ch = str(self.config.channels)
        system = platform.system().lower()
        if system == "linux":
            in_args = ["-f", "pulse", "-i", self.config.device]
        elif system == "darwin":
            in_args = ["-f", "avfoundation", "-i", f":{self.config.macos_device}"]
        elif system == "windows":
            in_args = ["-f", "dshow", "-i", f"audio={self.config.device}"]
        else:
            in_args = ["-f", "pulse", "-i", self.config.device]
        af = (
            f"silencedetect=noise={self.config.vad_noise_db}dB:"
            f"d={self.config.vad_silence_secs}"
        )
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "info",   # silencedetect logs at info level
            "-y",
            *in_args,
            "-ac", ch,
            "-ar", sr,
            "-af", af,
            "-acodec", "pcm_s16le",
            "-t", str(self.config.vad_max_secs),
            str(out),
        ]

    # ------------------------------------------------------------------ #
    # Command building
    # ------------------------------------------------------------------ #

    def _build_cmd(self, out: Path, *, max_seconds: float) -> list[str]:
        sr = str(self.config.sample_rate)
        ch = str(self.config.channels)
        if self._tool == "ffmpeg":
            system = platform.system().lower()
            if system == "linux":
                # Prefer pulse since most desktop installs run pipewire
                # with pulse compatibility. ALSA fallback is automatic
                # because ffmpeg picks the first reachable input.
                in_args = ["-f", "pulse", "-i", self.config.device]
            elif system == "darwin":
                in_args = ["-f", "avfoundation", "-i", f":{self.config.macos_device}"]
            elif system == "windows":
                in_args = ["-f", "dshow", "-i", f"audio={self.config.device}"]
            else:
                in_args = ["-f", "pulse", "-i", self.config.device]
            return [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-y",
                *in_args,
                "-ac", ch,
                "-ar", sr,
                "-acodec", "pcm_s16le",
                "-t", str(max_seconds),
                str(out),
            ]
        if self._tool == "arecord":
            return [
                "arecord",
                "-q",
                "-D", self.config.device,
                "-f", "S16_LE",
                "-r", sr,
                "-c", ch,
                "-t", "wav",
                "-d", str(int(max_seconds)),
                str(out),
            ]
        # parec writes raw PCM; we re-wrap to WAV after stop.
        return [
            "parec",
            f"--rate={sr}",
            "--format=s16le",
            f"--channels={ch}",
            "--file-format=raw",
            str(out),
        ]

    # ------------------------------------------------------------------ #
    # parec helper: raw PCM → WAV
    # ------------------------------------------------------------------ #

    def _wrap_parec_raw(self, path: Path) -> None:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return
        sr = self.config.sample_rate
        ch = self.config.channels
        bits = 16
        byte_rate = sr * ch * bits // 8
        block_align = ch * bits // 8
        data_size = len(raw)
        header = b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE"
        header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, ch, sr, byte_rate, block_align, bits)
        header += b"data" + struct.pack("<I", data_size)
        path.write_bytes(header + raw)

