"""
Serial command queue: enforces FIFO, one-in-flight-per-scope dispatch into
the FoundryClient. Guarantees that effects from a single scope (e.g. a
particular scene) are applied in the order they were submitted, with
backpressure (``QueueFullError``) and per-command timeout.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from queue import Empty, Queue
from time import monotonic
from typing import Any, Callable

from ai_dm.foundry.client import FoundryClient
from ai_dm.foundry.errors import QueueFullError

logger = logging.getLogger("ai_dm.foundry.queue")

GLOBAL_SCOPE = "global"


@dataclass
class _QueuedItem:
    fn: Callable[[], Any]
    future: Future
    enqueued_at: float
    deadline: float | None
    label: str = field(default="")


@dataclass
class QueueStats:
    submitted: int = 0
    completed: int = 0
    failed: int = 0
    timed_out_in_queue: int = 0
    backpressure_rejects: int = 0


class SerialCommandQueue:
    """
    One worker thread per scope. Submitting a command for a given
    ``scope_key`` returns a :class:`Future` that resolves to the response
    dict (or carries an exception).
    """

    def __init__(
        self,
        client: FoundryClient,
        *,
        max_pending: int = 32,
        default_timeout: float = 10.0,
    ) -> None:
        self.client = client
        self._max_pending = max_pending
        self._default_timeout = default_timeout
        self._lock = threading.Lock()
        self._queues: dict[str, Queue[_QueuedItem]] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._shutdown = threading.Event()
        self.stats = QueueStats()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def submit(
        self,
        payload: dict[str, Any],
        *,
        scope_key: str = GLOBAL_SCOPE,
        timeout: float | None = None,
        correlation_id: str | None = None,
    ) -> Future:
        effective_timeout = timeout if timeout is not None else self._default_timeout
        return self._enqueue(
            scope_key=scope_key,
            label=str(payload.get("type", "command")),
            timeout=effective_timeout,
            fn=lambda: self.client.request(
                payload,
                timeout=effective_timeout,
                correlation_id=correlation_id,
            ),
        )

    def submit_batch(
        self,
        payloads: list[dict[str, Any]],
        *,
        scope_key: str = GLOBAL_SCOPE,
        timeout: float | None = None,
        correlation_id: str | None = None,
    ) -> Future:
        effective_timeout = timeout if timeout is not None else self._default_timeout
        return self._enqueue(
            scope_key=scope_key,
            label=f"batch[{len(payloads)}]",
            timeout=effective_timeout,
            fn=lambda: self.client.request_batch(
                payloads,
                timeout=effective_timeout,
                correlation_id=correlation_id,
            ),
        )

    def shutdown(self, wait: bool = True) -> None:
        self._shutdown.set()
        if wait:
            with self._lock:
                workers = list(self._workers.values())
            for w in workers:
                w.join(timeout=5.0)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _enqueue(
        self,
        *,
        scope_key: str,
        label: str,
        timeout: float,
        fn: Callable[[], Any],
    ) -> Future:
        if self._shutdown.is_set():
            raise RuntimeError("queue is shut down")

        with self._lock:
            queue = self._queues.get(scope_key)
            if queue is None:
                queue = Queue(maxsize=self._max_pending)
                self._queues[scope_key] = queue
                worker = threading.Thread(
                    target=self._worker_loop,
                    name=f"foundry-queue-{scope_key}",
                    args=(scope_key, queue),
                    daemon=True,
                )
                self._workers[scope_key] = worker
                worker.start()

        future: Future = Future()
        deadline = monotonic() + timeout if timeout > 0 else None
        item = _QueuedItem(
            fn=fn,
            future=future,
            enqueued_at=monotonic(),
            deadline=deadline,
            label=label,
        )
        try:
            queue.put_nowait(item)
        except Exception as exc:
            self.stats.backpressure_rejects += 1
            raise QueueFullError(
                f"queue for scope {scope_key!r} is full ({self._max_pending} pending)"
            ) from exc

        self.stats.submitted += 1
        return future

    def _worker_loop(self, scope_key: str, queue: Queue[_QueuedItem]) -> None:
        while not self._shutdown.is_set():
            try:
                item = queue.get(timeout=0.25)
            except Empty:
                continue


            # Pre-dispatch deadline check.
            if item.deadline is not None and monotonic() > item.deadline:
                self.stats.timed_out_in_queue += 1
                item.future.set_exception(
                    TimeoutError(
                        f"{item.label} timed out before dispatch (waited "
                        f"{monotonic() - item.enqueued_at:.2f}s in scope {scope_key})"
                    )
                )
                continue

            try:
                result = item.fn()
                item.future.set_result(result)
                self.stats.completed += 1
            except BaseException as exc:  # noqa: BLE001 — propagate to caller via Future
                item.future.set_exception(exc)
                self.stats.failed += 1

    def queue_depth(self, scope_key: str = GLOBAL_SCOPE) -> int:
        with self._lock:
            q = self._queues.get(scope_key)
        return q.qsize() if q is not None else 0


