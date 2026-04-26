"""Smoke test: relay supervisor + eager-connected client deliver a chat
event end-to-end over a real WebSocket loop.

This catches the class of bug where ``/act`` chat messages "don't
affect the game loop" because Python connected lazily and never
heard the inbound event.
"""
from __future__ import annotations

import json
import time

import pytest

from ai_dm.foundry.client import FoundryClient
from ai_dm.foundry.relay_supervisor import RelaySupervisor
from ai_dm.foundry.socket_bridge import SocketBridge
from ai_dm.orchestration.event_bus import EventBus

websockets = pytest.importorskip("websockets")
from websockets.sync.client import connect as ws_connect  # noqa: E402


def _free_port() -> int:
    import socket as _socket

    s = _socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_relay_supervisor_delivers_inbound_event_eagerly():
    port = _free_port()
    supervisor = RelaySupervisor(host="127.0.0.1", port=port)
    supervisor.start(wait_timeout=2.0)
    assert supervisor.running

    try:
        bus = EventBus()
        client = FoundryClient(url=f"ws://127.0.0.1:{port}")
        SocketBridge(client, bus).connect()
        # Eager connect — the whole point of this test.
        assert client.try_connect(retries=10, delay=0.1)

        received = []
        bus.subscribe("foundry.player_input", received.append)

        # Open a second WebSocket pretending to be Foundry and push a
        # player_input event. The relay should forward it to Python,
        # the receive loop should hand it to ``on_push``, the bridge
        # should re-publish it on the bus, and our subscriber wakes.
        with ws_connect(f"ws://127.0.0.1:{port}") as foundry_ws:
            foundry_ws.send(json.dumps({"type": "hello", "client": "foundry"}))
            ack = json.loads(foundry_ws.recv())
            assert ack["type"] == "hello_ack"
            foundry_ws.send(json.dumps({
                "type": "event",
                "event": "player_input",
                "event_id": "evt-smoke-1",
                "payload": {
                    "user_id": "u-1",
                    "actor_id": "a-1",
                    "text": "open the door",
                },
            }))

            # Wait up to 1s for delivery.
            deadline = time.time() + 1.0
            while time.time() < deadline and not received:
                time.sleep(0.02)

        assert received, "player_input event was not delivered to the EventBus"
        assert received[0]["actor_id"] == "a-1"
        assert received[0]["text"] == "open the door"
    finally:
        try:
            client.stop_reconnect()
        except Exception:
            pass
        supervisor.stop()

