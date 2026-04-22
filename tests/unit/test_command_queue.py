import threading
import time
from concurrent.futures import wait

import pytest

from ai_dm.foundry.command_queue import SerialCommandQueue
from ai_dm.foundry.errors import QueueFullError


class FakeClient:
    def __init__(self, *, latency: float = 0.0) -> None:
        self.latency = latency
        self.calls: list[dict] = []
        self._lock = threading.Lock()
        self.in_flight = 0
        self.peak_in_flight = 0

    def request(self, payload, timeout=10.0, correlation_id=None):
        with self._lock:
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            time.sleep(self.latency)
            with self._lock:
                self.calls.append(payload)
            return {"type": "result", "result": {"ok": True, "echo": payload}}
        finally:
            with self._lock:
                self.in_flight -= 1

    def request_batch(self, payloads, timeout=10.0, correlation_id=None):
        return {"type": "result", "result": [{"ok": True} for _ in payloads]}


def test_queue_preserves_fifo_within_scope():
    client = FakeClient(latency=0.01)
    q = SerialCommandQueue(client, max_pending=10)
    futures = [q.submit({"type": "x", "i": i}, scope_key="s1") for i in range(5)]
    wait(futures, timeout=5)
    q.shutdown()

    seen = [c["i"] for c in client.calls]
    assert seen == [0, 1, 2, 3, 4]


def test_queue_serialises_per_scope():
    client = FakeClient(latency=0.05)
    q = SerialCommandQueue(client, max_pending=10)
    futures = [q.submit({"type": "x", "i": i}, scope_key="s1") for i in range(4)]
    wait(futures, timeout=5)
    q.shutdown()
    assert client.peak_in_flight == 1


def test_queue_runs_separate_scopes_concurrently():
    client = FakeClient(latency=0.05)
    q = SerialCommandQueue(client, max_pending=10)
    futures = [
        q.submit({"type": "x", "i": i}, scope_key=f"s{i}") for i in range(4)
    ]
    wait(futures, timeout=5)
    q.shutdown()
    assert client.peak_in_flight >= 2


def test_queue_backpressure_raises():
    # block worker forever so the queue fills up
    block = threading.Event()

    class Blocking:
        def request(self, *_a, **_k):
            block.wait()
            return {"type": "result", "result": {"ok": True}}

        def request_batch(self, *_a, **_k):
            block.wait()
            return {"type": "result", "result": []}

    q = SerialCommandQueue(Blocking(), max_pending=2)  # type: ignore[arg-type]
    rejected = 0
    for i in range(10):
        try:
            q.submit({"type": "x", "i": i}, scope_key="s")
        except QueueFullError:
            rejected += 1
    assert rejected >= 1
    block.set()
    q.shutdown(wait=False)


def test_queue_timeout_before_dispatch():
    block = threading.Event()

    class Blocking:
        def request(self, *_a, **_k):
            block.wait()
            return {"type": "result", "result": {"ok": True}}

        def request_batch(self, *_a, **_k):
            return {"type": "result", "result": []}

    q = SerialCommandQueue(Blocking(), max_pending=10)  # type: ignore[arg-type]
    q.submit({"type": "first"}, scope_key="s", timeout=10.0)
    fut = q.submit({"type": "second"}, scope_key="s", timeout=0.05)
    time.sleep(0.2)
    block.set()
    with pytest.raises(TimeoutError):
        fut.result(timeout=2)
    q.shutdown(wait=False)

