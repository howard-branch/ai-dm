"""In-process audio queue.

Items are synthesised on a background thread and emitted to a sink
callable (or simply cached in memory for tests). Three priority lanes
exist — ``system`` > ``narration`` > ``dialogue`` — but within a lane the
order is FIFO. ``interrupt()`` clears all queued items; the in-flight
synthesis (if any) finishes but its output is discarded.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from queue import Empty, PriorityQueue
from typing import Callable, Literal

from ai_dm.audio.tts import NullBackend, TTSBackend

logger = logging.getLogger("ai_dm.audio.queue")

Priority = Literal["dialogue", "narration", "system"]
_PRIO_ORDER = {"system": 0, "narration": 1, "dialogue": 2}

Sink = Callable[["AudioItem", bytes], None]


@dataclass(order=False)
class AudioItem:
    text: str
    voice: str | None = None
    priority: Priority = "narration"
    speaker_id: str | None = None
    tag: str | None = None
    meta: dict = field(default_factory=dict)


class AudioQueue:
    def __init__(
        self,
        backend: TTSBackend | None = None,
        *,
        sink: Sink | None = None,
        autostart: bool = True,
    ) -> None:
        self.backend = backend or NullBackend()
        self.sink = sink
        self._queue: "PriorityQueue[tuple[int, int, AudioItem]]" = PriorityQueue()
        self._counter = 0
        self._counter_lock = threading.Lock()
        self._stop = threading.Event()
        self._interrupted = threading.Event()
        self._worker: threading.Thread | None = None
        self._processing = False
        if autostart:
            self.start()

    # ------------------------------------------------------------------ #

    def enqueue(self, item: AudioItem | str, *, priority: Priority | None = None) -> None:
        if isinstance(item, str):
            item = AudioItem(text=item, priority=priority or "narration")
        elif priority is not None:
            item.priority = priority
        with self._counter_lock:
            self._counter += 1
            tie = self._counter
        self._queue.put((_PRIO_ORDER[item.priority], tie, item))

    def interrupt(self) -> None:
        """Clear pending items and signal the worker to drop in-flight output."""
        self._interrupted.set()
        try:
            while True:
                self._queue.get_nowait()
        except Empty:
            pass

    def pending(self) -> int:
        return self._queue.qsize()

    def join(self, timeout: float | None = None) -> None:
        end = None if timeout is None else time.monotonic() + timeout
        while self._queue.qsize() > 0 or self._processing:
            if end is not None and time.monotonic() > end:
                return
            time.sleep(0.01)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._loop, name="audio-queue", daemon=True
        )
        self._worker.start()

    def shutdown(self, wait: bool = True) -> None:
        self._stop.set()
        if wait and self._worker is not None:
            self._worker.join(timeout=2.0)

    # ------------------------------------------------------------------ #

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                # Short timeout keeps shutdown responsive while still
                # picking up newly enqueued items within ~10ms — important
                # for time-to-first-sound after the narrator emits.
                _prio, _tie, item = self._queue.get(timeout=0.01)
            except Empty:
                continue
            self._processing = True
            self._interrupted.clear()
            self._handle_item(item)
            self._processing = False

    # ------------------------------------------------------------------ #

    def _handle_item(self, item: AudioItem) -> None:
        # Streaming fast-path: backend yields chunks AND the sink
        # advertises a stream entry point. This lets the player begin
        # decoding while later chunks are still being downloaded from
        # the TTS provider — typically saves multiple seconds on long
        # narrations.
        stream_fn = getattr(self.backend, "stream", None)
        sink_stream = getattr(self.sink, "stream", None) if self.sink else None
        if callable(stream_fn) and callable(sink_stream):
            try:
                chunks = stream_fn(item.text, item.voice)
                if not self._interrupted.is_set():
                    sink_stream(item, chunks)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("TTS stream failed (%s); falling back to buffered", exc)

        # Buffered path.
        try:
            audio = self.backend.synthesize(item.text, item.voice)
        except Exception as exc:  # noqa: BLE001
            logger.warning("TTS synthesise failed: %s", exc)
            audio = b""
        if not self._interrupted.is_set() and self.sink is not None:
            try:
                self.sink(item, audio)
            except Exception as exc:  # noqa: BLE001
                logger.warning("audio sink failed: %s", exc)
