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
    # Subset of foundry_clients whose hello declared is_gm=true. Used so
    # Python can fail-fast when no GM browser is available to execute
    # world-mutating commands.
    foundry_gm_clients: set[ServerConnection] = field(default_factory=set)
    # Per-client metadata captured from hello (user_name, is_gm). Keyed
    # by websocket so we can include it in the `who` census.
    client_meta: dict[ServerConnection, dict[str, Any]] = field(default_factory=dict)


class FoundryRelayServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        seen_id_lru_size: int = 1024,
        send_timeout: float = 2.0,
    ) -> None:
        self.host = host
        self.port = port
        self.state = RelayState()
        # Per-direction dedupe of forwarded request ids.
        self._seen_outbound: OrderedDict[str, None] = OrderedDict()
        self._seen_results: OrderedDict[str, None] = OrderedDict()
        self._seen_events: OrderedDict[str, None] = OrderedDict()
        self._lru_size = seen_id_lru_size
        # Per-client send timeout used by ``_broadcast``. A stuck client
        # is dropped after this many seconds rather than blocking every
        # other message in the event loop.
        self._send_timeout = send_timeout

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
                if bool(msg.get("is_gm")):
                    self.state.foundry_gm_clients.add(websocket)
            else:
                await websocket.send(json.dumps({
                    "type": "error",
                    "error": "unknown_client_type",
                }))
                return

            self.state.client_meta[websocket] = {
                "type": client_type,
                "user_name": msg.get("user_name"),
                "user_id": msg.get("user_id"),
                "is_gm": bool(msg.get("is_gm")),
            }

            logger.info(
                "client connected: type=%s user=%s is_gm=%s peer=%s "
                "(python=%d foundry=%d gm=%d)",
                client_type, msg.get("user_name"), bool(msg.get("is_gm")), peer,
                len(self.state.python_clients),
                len(self.state.foundry_clients),
                len(self.state.foundry_gm_clients),
            )

            await websocket.send(json.dumps({
                "type": "hello_ack",
                "client": client_type,
                "foundry_count": len(self.state.foundry_clients),
                "foundry_gm_count": len(self.state.foundry_gm_clients),
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
                self.state.foundry_gm_clients.discard(websocket)
            self.state.client_meta.pop(websocket, None)
            if client_type is not None:
                logger.info(
                    "client disconnected: type=%s peer=%s "
                    "(python=%d foundry=%d gm=%d)",
                    client_type, peer,
                    len(self.state.python_clients),
                    len(self.state.foundry_clients),
                    len(self.state.foundry_gm_clients),
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

        if msg_type == "who":
            # Census query: how many clients of each type are connected,
            # and which Foundry users (with isGM flag). Used by Python
            # to fail-fast at startup when no GM browser is available.
            await websocket.send(json.dumps({
                "type": "who_ack",
                "python_count": len(self.state.python_clients),
                "foundry_count": len(self.state.foundry_clients),
                "foundry_gm_count": len(self.state.foundry_gm_clients),
                "foundry_clients": [
                    {
                        "user_name": meta.get("user_name"),
                        "user_id": meta.get("user_id"),
                        "is_gm": meta.get("is_gm"),
                    }
                    for ws, meta in self.state.client_meta.items()
                    if meta.get("type") == "foundry"
                ],
            }))
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

        if msg_type == "event":
            # Bidirectional, fire-and-forget channel for chat-driven
            # input (`player_input`, `player_intent`) and outbound
            # `narration` pushes. Deduped by an optional `event_id`.
            import uuid as _uuid

            event_id = msg.get("event_id") or f"evt-{_uuid.uuid4().hex}"
            if self._mark_seen(self._seen_events, event_id):
                logger.warning("dropping duplicate event id=%s", event_id)
                return

            forward = {
                "type": "event",
                "event": msg.get("event"),
                "payload": msg.get("payload") or {},
                "event_id": event_id,
            }
            if client_type == "foundry":
                await self._broadcast_to_python(forward)
            else:
                # Broadcast to every connected Foundry client. The JS
                # side performs an in-browser election (prefer the GM,
                # otherwise the lowest user id) so exactly one client
                # creates the ChatMessage; Foundry's own server then
                # replicates that message to every other tab. We do
                # NOT GM-route here at the relay because a player-only
                # session would otherwise see no narration at all.
                await self._broadcast_to_foundry(forward)
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
        """Send ``payload`` to every client concurrently with a per-client
        timeout. Any client whose send hangs longer than
        ``_send_timeout`` seconds is considered wedged and dropped — a
        single stuck Foundry tab (paused background tab, OS-level
        throttling, etc.) used to block the whole event loop, which
        broke ``who()`` *and* every subsequent command.
        """
        if not clients:
            return
        text = json.dumps(payload)
        # Snapshot — clients may be mutated mid-iteration.
        targets = list(clients)

        async def _send_one(client: ServerConnection) -> ServerConnection | None:
            try:
                await asyncio.wait_for(client.send(text), timeout=self._send_timeout)
                return None
            except asyncio.TimeoutError:
                logger.warning(
                    "relay: dropping wedged client (send timeout %.1fs) — "
                    "peer=%s meta=%s",
                    self._send_timeout,
                    getattr(client, "remote_address", None),
                    self.state.client_meta.get(client),
                )
                return client
            except Exception as exc:  # noqa: BLE001
                logger.info("relay: dropping client after send error: %s", exc)
                return client

        results = await asyncio.gather(
            *(_send_one(c) for c in targets),
            return_exceptions=False,
        )
        for dead in results:
            if dead is not None:
                clients.discard(dead)
                self.state.python_clients.discard(dead)
                self.state.foundry_clients.discard(dead)
                self.state.foundry_gm_clients.discard(dead)
                self.state.client_meta.pop(dead, None)
                try:
                    await dead.close()
                except Exception:  # noqa: BLE001
                    pass


async def main() -> None:
    server = FoundryRelayServer()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())