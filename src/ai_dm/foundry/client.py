from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import Any

from websockets.sync.client import connect

from ai_dm.foundry.protocol import (
    build_batch_envelope,
    build_command_envelope,
    is_valid_request_id,
)

logger = logging.getLogger("ai_dm.foundry.client")


@dataclass
class PendingRequest:
    event: threading.Event
    command_type: str
    issued_at: float
    correlation_id: str | None = None
    response: dict[str, Any] | None = None


@dataclass
class ClientStats:
    sent: int = 0
    received: int = 0
    duplicates: int = 0
    orphans: int = 0
    timeouts: int = 0


class FoundryClient:
    """
    Python-side WebSocket client with request/response correlation.

    Every outbound message carries a ``request_id``. Inbound results are
    matched back to a :class:`PendingRequest`. Results without a matching
    pending request are tracked as ``orphans`` and dropped; replays for an
    already-completed request are tracked as ``duplicates``.
    """

    _COMPLETED_LRU_SIZE = 512

    def __init__(self, url: str = "ws://127.0.0.1:8765") -> None:
        self.url = url
        self._conn = None
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._pending: dict[str, PendingRequest] = {}
        self._completed: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._receiver_thread: threading.Thread | None = None
        self._connected = False
        self.stats = ClientStats()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def send(self, payload: dict[str, Any], *, correlation_id: str | None = None) -> str:
        envelope = build_command_envelope(payload, correlation_id=correlation_id)
        self._send_envelope(envelope)
        return envelope["request_id"]

    def send_batch(
        self,
        payloads: list[dict[str, Any]],
        *,
        correlation_id: str | None = None,
    ) -> str:
        envelope = build_batch_envelope(payloads, correlation_id=correlation_id)
        self._send_envelope(envelope)
        return envelope["request_id"]

    def request(
        self,
        payload: dict[str, Any],
        timeout: float = 10.0,
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        envelope = build_command_envelope(payload, correlation_id=correlation_id)
        return self._await_envelope(
            envelope,
            command_type=str(payload.get("type", "unknown")),
            timeout=timeout,
            correlation_id=correlation_id,
        )

    def request_batch(
        self,
        payloads: list[dict[str, Any]],
        timeout: float = 10.0,
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        envelope = build_batch_envelope(payloads, correlation_id=correlation_id)
        return self._await_envelope(
            envelope,
            command_type="batch",
            timeout=timeout,
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _await_envelope(
        self,
        envelope: dict[str, Any],
        *,
        command_type: str,
        timeout: float,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        request_id = envelope["request_id"]
        pending = PendingRequest(
            event=threading.Event(),
            command_type=command_type,
            issued_at=monotonic(),
            correlation_id=correlation_id,
        )

        with self._state_lock:
            self._pending[request_id] = pending

        try:
            self._send_envelope(envelope)
        except Exception:
            with self._state_lock:
                self._pending.pop(request_id, None)
            raise

        if not pending.event.wait(timeout=timeout):
            with self._state_lock:
                self._pending.pop(request_id, None)
                self.stats.timeouts += 1
            raise TimeoutError(
                f"Timed out waiting for Foundry response to {command_type} "
                f"(request_id={request_id})"
            )

        assert pending.response is not None
        return pending.response

    def _send_envelope(self, envelope: dict[str, Any]) -> None:
        self._ensure_connected()
        self._send_json(envelope)
        self.stats.sent += 1
        logger.debug(
            "sent %s request_id=%s correlation=%s",
            envelope.get("type"),
            envelope.get("request_id"),
            envelope.get("correlation_id"),
        )

    def _ensure_connected(self) -> None:
        with self._state_lock:
            if self._connected and self._conn is not None:
                return

            conn = connect(self.url)
            conn.send(json.dumps({
                "type": "hello",
                "client": "python",
            }))

            raw = conn.recv()
            msg = json.loads(raw)
            if msg.get("type") != "hello_ack":
                raise RuntimeError(f"Relay handshake failed: {msg}")

            self._conn = conn
            self._connected = True

            self._receiver_thread = threading.Thread(
                target=self._recv_loop,
                name="foundry-relay-recv",
                daemon=True,
            )
            self._receiver_thread.start()

            logger.info("connected to relay at %s", self.url)

    def _send_json(self, message: dict[str, Any]) -> None:
        with self._send_lock:
            assert self._conn is not None
            self._conn.send(json.dumps(message))

    def _recv_loop(self) -> None:
        assert self._conn is not None
        try:
            while True:
                raw = self._conn.recv()
                msg = json.loads(raw)
                self._handle_incoming(msg)
        except Exception as exc:
            logger.warning("relay receive loop stopped: %s", exc)
            with self._state_lock:
                self._connected = False
                for pending in self._pending.values():
                    pending.response = {
                        "type": "error",
                        "error": f"connection_lost: {exc}",
                    }
                    pending.event.set()
                self._pending.clear()

    def _handle_incoming(self, msg: dict[str, Any]) -> None:
        msg_type = msg.get("type")

        if msg_type == "result":
            request_id = msg.get("request_id")
            if not is_valid_request_id(request_id):
                logger.warning("ignoring result without request_id: %s", msg)
                return

            with self._state_lock:
                pending = self._pending.pop(request_id, None)
                self.stats.received += 1

                if pending is None:
                    if request_id in self._completed:
                        self.stats.duplicates += 1
                        logger.warning("duplicate result for %s — dropped", request_id)
                    else:
                        self.stats.orphans += 1
                        logger.warning("no pending request for result %s", request_id)
                    return

                self._completed[request_id] = msg
                if len(self._completed) > self._COMPLETED_LRU_SIZE:
                    self._completed.popitem(last=False)

            pending.response = msg
            pending.event.set()
            return

        if msg_type == "pong":
            return

        if msg_type == "event":
            # Foundry-originated push event (Phase 3 inbound bridge).
            cb = self.on_push
            if cb is not None:
                try:
                    cb(msg)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("on_push handler failed: %s", exc)
            return

        logger.debug("unhandled message from relay: %s", msg)

    # ------------------------------------------------------------------ #
    # Test helpers
    # ------------------------------------------------------------------ #

    def _inject_result(self, msg: dict[str, Any]) -> None:
        """Feed a result envelope as if it came from the relay (test only)."""
        self._handle_incoming(msg)

