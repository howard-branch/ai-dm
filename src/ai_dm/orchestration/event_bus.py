"""Synchronous in-process event bus."""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger("ai_dm.events")

Handler = Callable[[dict[str, Any]], None]


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self.history: list[tuple[str, dict[str, Any]]] = []
        self._max_history = 500

    def subscribe(self, event_name: str, handler: Handler) -> Callable[[], None]:
        """Register a handler. Returns an unsubscribe callable."""
        with self._lock:
            self._handlers[event_name].append(handler)

        def _unsubscribe() -> None:
            with self._lock:
                if handler in self._handlers.get(event_name, ()):
                    self._handlers[event_name].remove(handler)

        return _unsubscribe

    def publish(self, event_name: str, payload: dict[str, Any] | None = None) -> None:
        payload = payload or {}
        with self._lock:
            handlers = list(self._handlers.get(event_name, ()))
            self.history.append((event_name, payload))
            if len(self.history) > self._max_history:
                self.history = self.history[-self._max_history:]
        for h in handlers:
            try:
                h(payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("event handler for %s failed: %s", event_name, exc)
