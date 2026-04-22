"""Speech-to-text transcription.

Two backends, picked in order of preference at construct time:

1. **OpenAI Whisper API** — used when ``OPENAI_API_KEY`` is set.
   Sends the WAV to ``audio.transcriptions``. Model can be overridden
   with ``OPENAI_TRANSCRIBE_MODEL`` (default ``whisper-1``, but
   ``gpt-4o-mini-transcribe`` and ``gpt-4o-transcribe`` are accepted
   verbatim by the same endpoint).
2. **faster-whisper** (local) — used when the package is installed and
   no API key is present, or when the API call fails. Model size is
   controlled with ``AI_DM_WHISPER_MODEL`` (default ``base``).

Both return plain text. On total failure the empty string is returned
and the caller can decide what to do (typically print a warning).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("ai_dm.audio.transcribe")


class TranscriberBackend(Protocol):
    name: str

    def transcribe(self, wav_path: Path) -> str: ...

    def is_available(self) -> bool: ...


class OpenAIWhisperBackend:
    name = "openai-whisper"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        language: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._model = model or os.environ.get("OPENAI_TRANSCRIBE_MODEL") or "whisper-1"
        # Default to English; Whisper otherwise auto-detects and frequently
        # mis-classifies short / quiet utterances as Welsh, French, etc.
        # Override with AI_DM_TRANSCRIBE_LANG (ISO 639-1, or empty string
        # to force auto-detect).
        env_lang = os.environ.get("AI_DM_TRANSCRIBE_LANG")
        if language is not None:
            self._language: str | None = language or None
        elif env_lang is not None:
            self._language = env_lang or None
        else:
            self._language = "en"
        self._client = None  # lazy

    def is_available(self) -> bool:
        return bool(self._api_key)

    def _get_client(self):
        if self._client is not None:
            return self._client
        from openai import OpenAI  # type: ignore
        self._client = OpenAI(api_key=self._api_key)
        return self._client

    def transcribe(self, wav_path: Path) -> str:
        if not self.is_available():
            raise RuntimeError("OPENAI_API_KEY not set")
        client = self._get_client()
        kwargs: dict = {"model": self._model}
        if self._language:
            kwargs["language"] = self._language
        with wav_path.open("rb") as fh:
            resp = client.audio.transcriptions.create(file=fh, **kwargs)
        text = getattr(resp, "text", None) or ""
        return text.strip()


class FasterWhisperBackend:
    name = "faster-whisper"

    def __init__(self, *, model_size: str | None = None, language: str | None = None) -> None:
        self.model_size = model_size or os.environ.get("AI_DM_WHISPER_MODEL") or "base"
        env_lang = os.environ.get("AI_DM_TRANSCRIBE_LANG")
        if language is not None:
            self._language: str | None = language or None
        elif env_lang is not None:
            self._language = env_lang or None
        else:
            self._language = "en"
        self._model = None
        self._available: bool | None = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import faster_whisper  # noqa: F401  type: ignore[import-not-found]
            self._available = True
        except Exception:  # noqa: BLE001
            self._available = False
        return self._available

    def _load(self):
        if self._model is not None:
            return self._model
        from faster_whisper import WhisperModel  # type: ignore
        # CPU int8 is the fastest sane default on a typical dev box.
        self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
        return self._model

    def transcribe(self, wav_path: Path) -> str:
        if not self.is_available():
            raise RuntimeError("faster-whisper not installed")
        model = self._load()
        kwargs: dict = {"beam_size": 1}
        if self._language:
            kwargs["language"] = self._language
        segments, _info = model.transcribe(str(wav_path), **kwargs)
        return " ".join(seg.text.strip() for seg in segments).strip()


class NullTranscriber:
    name = "null"

    def is_available(self) -> bool:
        return True

    def transcribe(self, wav_path: Path) -> str:  # noqa: ARG002
        return ""


class Transcriber:
    """Try the primary backend, fall back to the secondary on failure."""

    def __init__(
        self,
        primary: TranscriberBackend | None = None,
        fallback: TranscriberBackend | None = None,
    ) -> None:
        self.primary = primary or OpenAIWhisperBackend()
        self.fallback = fallback or FasterWhisperBackend()

    def is_available(self) -> bool:
        return self.primary.is_available() or self.fallback.is_available()

    def active_backend_name(self) -> str:
        if self.primary.is_available():
            return self.primary.name
        if self.fallback.is_available():
            return self.fallback.name
        return "none"

    def transcribe(self, wav_path: Path) -> str:
        if self.primary.is_available():
            try:
                return self.primary.transcribe(wav_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "transcription primary %r failed (%s) — falling back to %r",
                    self.primary.name, exc, self.fallback.name,
                )
        if self.fallback.is_available():
            try:
                return self.fallback.transcribe(wav_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("transcription fallback %r failed: %s", self.fallback.name, exc)
        return ""


def default_transcriber() -> Transcriber:
    return Transcriber(
        primary=OpenAIWhisperBackend(),
        fallback=FasterWhisperBackend(),
    )

