"""Inbound Foundry → local-EventBus bridge.

The :class:`FoundryClient` already has a receive loop that owns the
WebSocket. We attach a push-callback that re-publishes any inbound
``{"type":"event"}`` envelope onto the in-process :class:`EventBus`,
prefixed with ``foundry.``.

Outbound traffic continues to flow through ``CommandRouter`` → queue.
"""
from __future__ import annotations

import logging
from typing import Any

from ai_dm.foundry.client import FoundryClient
from ai_dm.orchestration.event_bus import EventBus

logger = logging.getLogger("ai_dm.foundry.bridge")


class SocketBridge:
    """Subscribes to Foundry pushes and re-emits them as local events."""

    def __init__(self, client: FoundryClient, event_bus: EventBus) -> None:
        self.client = client
        self.event_bus = event_bus
        self._connected = False

    def connect(self) -> None:
        """Install the push handler on the client. Idempotent."""
        if self._connected:
            return
        self.client.on_push = self._on_push
        self._connected = True
        logger.info("foundry inbound bridge installed")

    def disconnect(self) -> None:
        if not self._connected:
            return
        if self.client.on_push is self._on_push:
            self.client.on_push = None
        self._connected = False

    # ------------------------------------------------------------------ #

    def _on_push(self, envelope: dict[str, Any]) -> None:
        name = envelope.get("event")
        if not isinstance(name, str) or not name:
            logger.warning("push event missing 'event' name: %s", envelope)
            return
        payload = envelope.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        payload = dict(payload)
        payload.setdefault("origin", "foundry")
        try:
            self.event_bus.publish(f"foundry.{name}", payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("re-publish foundry.%s failed: %s", name, exc)
