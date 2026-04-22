from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from websockets.asyncio.server import ServerConnection, serve

from ai_dm.foundry.protocol import is_valid_request_id

logger = logging.getLogger("ai_dm.foundry.relay")


@dataclass
class RelayState:
    python_clients: set[ServerConnection] = field(default_factory=set)
    foundry_clients: set[ServerConnection] = field(default_factory=set)


class FoundryRelayServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        seen_id_lru_size: int = 1024,
    ) -> None:
        self.host = host
        self.port = port
        self.state = RelayState()
        # Per-direction dedupe of forwarded request ids.
        self._seen_outbound: OrderedDict[str, None] = OrderedDict()
        self._seen_results: OrderedDict[str, None] = OrderedDict()
        self._lru_size = seen_id_lru_size

    async def run(self) -> None:
        async with serve(self._handler, self.host, self.port):
            logger.info("Foundry relay listening on ws://%s:%d", self.host, self.port)
            await asyncio.Future()

    async def _handler(self, websocket: ServerConnection) -> None:
        client_type = None
        peer = getattr(websocket, "remote_address", None)
        try:
            raw = await websocket.recv()
            msg = json.loads(raw)

            if msg.get("type") != "hello":
                await websocket.send(json.dumps({
                    "type": "error",
                    "error": "first_message_must_be_hello",
                }))
                return

            client_type = msg.get("client")
            if client_type == "python":
                self.state.python_clients.add(websocket)
            elif client_type == "foundry":
                self.state.foundry_clients.add(websocket)
            else:
                await websocket.send(json.dumps({
                    "type": "error",
                    "error": "unknown_client_type",
                }))
                return

            logger.info(
                "client connected: type=%s peer=%s (python=%d foundry=%d)",
                client_type, peer,
                len(self.state.python_clients), len(self.state.foundry_clients),
            )

            await websocket.send(json.dumps({
                "type": "hello_ack",
                "client": client_type,
            }))

            async for raw in websocket:
                await self._handle_message(websocket, client_type, raw)

        except Exception as exc:
            logger.warning("relay connection error (peer=%s): %s", peer, exc)
        finally:
            if client_type == "python":
                self.state.python_clients.discard(websocket)
            elif client_type == "foundry":
                self.state.foundry_clients.discard(websocket)
            if client_type is not None:
                logger.info(
                    "client disconnected: type=%s peer=%s (python=%d foundry=%d)",
                    client_type, peer,
                    len(self.state.python_clients), len(self.state.foundry_clients),
                )

    async def _handle_message(
            self,
            websocket: ServerConnection,
            client_type: str,
            raw: str,
    ) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.send(json.dumps({
                "type": "error",
                "error": "invalid_json",
            }))
            return

        msg_type = msg.get("type")

        if msg_type == "ping":
            await websocket.send(json.dumps({"type": "pong"}))
            return

        if client_type == "python" and msg_type in ("command", "batch"):
            request_id = msg.get("request_id")
            if not is_valid_request_id(request_id):
                await websocket.send(json.dumps({
                    "type": "error",
                    "error": "missing_request_id",
                }))
                return

            if self._mark_seen(self._seen_outbound, request_id):
                logger.warning("dropping duplicate outbound %s id=%s", msg_type, request_id)
                return

            forward: dict[str, Any] = {
                "type": msg_type,
                "request_id": request_id,
            }
            if msg_type == "command":
                forward["command"] = msg.get("command")
            else:
                forward["commands"] = msg.get("commands", [])
            if "correlation_id" in msg:
                forward["correlation_id"] = msg["correlation_id"]

            await self._broadcast_to_foundry(forward)
            return

        if client_type == "foundry" and msg_type == "result":
            request_id = msg.get("request_id")
            if not is_valid_request_id(request_id):
                await websocket.send(json.dumps({
                    "type": "error",
                    "error": "missing_request_id",
                }))
                return

            if self._mark_seen(self._seen_results, request_id):
                logger.warning("dropping duplicate result id=%s", request_id)
                return

            await self._broadcast_to_python(msg)
            return

        await websocket.send(json.dumps({
            "type": "error",
            "error": f"unsupported_message:{msg_type}",
        }))

    def _mark_seen(self, lru: OrderedDict[str, None], request_id: str) -> bool:
        """Return True if ``request_id`` was already seen (caller should drop)."""
        if request_id in lru:
            lru.move_to_end(request_id)
            return True
        lru[request_id] = None
        if len(lru) > self._lru_size:
            lru.popitem(last=False)
        return False

    async def _broadcast_to_foundry(self, payload: dict[str, Any]) -> None:
        if not self.state.foundry_clients:
            logger.warning("no Foundry clients connected; dropping outbound message")
            return
        await self._broadcast(self.state.foundry_clients, payload)

    async def _broadcast_to_python(self, payload: dict[str, Any]) -> None:
        if not self.state.python_clients:
            return
        await self._broadcast(self.state.python_clients, payload)

    async def _broadcast(
            self,
            clients: set[ServerConnection],
            payload: dict[str, Any],
    ) -> None:
        text = json.dumps(payload)
        dead: list[ServerConnection] = []

        for client in clients:
            try:
                await client.send(text)
            except Exception:
                dead.append(client)

        for client in dead:
            clients.discard(client)


async def main() -> None:
    server = FoundryRelayServer()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())