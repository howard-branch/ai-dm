import asyncio

from ai_dm.foundry.ws_relay_server import FoundryRelayServer


async def main() -> None:
    server = FoundryRelayServer(host="127.0.0.1", port=8765)
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())