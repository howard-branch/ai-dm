"""Run the WebSocket relay server in a background thread.

The relay (``FoundryRelayServer``) is an asyncio server. Production
deployments may run it as a separate process via
``scripts/run_foundry_replay.py``, but for the all-in-one developer
workflow (``python -m ai_dm.main``) it's far easier to start it
in-process so a single command brings up the whole stack.

This module owns its own asyncio event loop on a daemon thread so it
doesn't fight with any other event loop the rest of the app might
later use.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time

from ai_dm.foundry.ws_relay_server import FoundryRelayServer

logger = logging.getLogger("ai_dm.foundry.relay_supervisor")


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


class RelaySupervisor:
    """Owns a background thread running ``FoundryRelayServer.run()``."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.host = host
        self.port = port
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: FoundryRelayServer | None = None
        self._started = threading.Event()
        self._reused_external = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, *, wait_timeout: float = 2.0) -> None:
        if self.running or self._reused_external:
            return
        # If the port is already bound (e.g. user manually launched
        # ``scripts/run_foundry_replay.py``) we just reuse it.
        if _port_in_use(self.host, self.port):
            logger.info(
                "relay port %s:%d already in use — assuming an external relay is running",
                self.host, self.port,
            )
            self._reused_external = True
            self._started.set()
            return

        self._thread = threading.Thread(
            target=self._run, name="foundry-relay", daemon=True
        )
        self._thread.start()
        if not self._started.wait(timeout=wait_timeout):
            logger.warning("relay supervisor did not signal start within %.1fs", wait_timeout)
        # Spin briefly for the listening socket to actually be reachable.
        deadline = time.time() + wait_timeout
        while time.time() < deadline:
            if _port_in_use(self.host, self.port):
                logger.info("foundry relay listening on ws://%s:%d", self.host, self.port)
                return
            time.sleep(0.05)
        logger.warning("relay does not appear to be accepting connections yet")

    def stop(self) -> None:
        if self._reused_external:
            return
        if self._loop is None or self._thread is None:
            return
        loop = self._loop

        async def _shutdown() -> None:
            # Cancel every task other than ourselves so the websockets
            # server tears its connections down cleanly before we stop
            # the loop. Without this, pytest sees PytestUnraisable
            # warnings from half-finished recv() coroutines.
            tasks = [
                t for t in asyncio.all_tasks(loop)
                if t is not asyncio.current_task()
            ]
            for t in tasks:
                t.cancel()
            for t in tasks:
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

        try:
            fut = asyncio.run_coroutine_threadsafe(_shutdown(), loop)
            try:
                fut.result(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
            loop.call_soon_threadsafe(loop.stop)
        except Exception:  # noqa: BLE001
            pass
        self._thread.join(timeout=2.0)
        self._thread = None
        self._loop = None

    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._server = FoundryRelayServer(host=self.host, port=self.port)
        try:
            self._started.set()
            loop.run_until_complete(self._server.run())
        except Exception as exc:  # noqa: BLE001
            logger.warning("relay supervisor crashed: %s", exc)
        finally:
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass

