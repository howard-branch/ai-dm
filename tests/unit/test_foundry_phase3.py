from unittest.mock import MagicMock

from ai_dm.foundry.client import FoundryClient
from ai_dm.foundry.journal import JournalService
from ai_dm.foundry.reconciler import Reconciler
from ai_dm.foundry.registry import FoundryRegistry
from ai_dm.foundry.snapshots import ActorSnapshot
from ai_dm.foundry.socket_bridge import SocketBridge
from ai_dm.foundry.sync_service import SyncService
from ai_dm.orchestration.event_bus import EventBus


class _StubClient:
    def __init__(self, response):
        self._response = response
        self.requests = []

    def request(self, payload, timeout=10.0, correlation_id=None):
        self.requests.append(payload)
        return self._response


def test_journal_create_returns_id():
    client = _StubClient({
        "type": "result",
        "result": {"ok": True, "command_type": "create_journal", "journalId": "j-1"},
    })
    js = JournalService(client=client)
    jid = js.create_entry("Recap", "hello")
    assert jid == "j-1"
    assert client.requests[0]["type"] == "create_journal"
    assert client.requests[0]["title"] == "Recap"


def test_journal_update_calls_relay():
    client = _StubClient({
        "type": "result",
        "result": {"ok": True, "command_type": "update_journal", "journalId": "j-1"},
    })
    js = JournalService(client=client)
    js.append_recap("j-1", "## later")
    assert client.requests[0]["journal_id"] == "j-1"
    assert client.requests[0]["content"] == "## later"


def test_sync_pull_actor():
    payload = {
        "type": "result",
        "result": {
            "ok": True,
            "snapshot": {"id": "a-1", "name": "Goblin", "hp": 7, "ac": 12},
        },
    }
    client = _StubClient(payload)
    sync = SyncService(client=client, registry=FoundryRegistry())
    snap = sync.pull_actor("a-1")
    assert isinstance(snap, ActorSnapshot)
    assert snap.hp == 7


def test_reconciler_diff_and_heal():
    bus = EventBus()
    registry = FoundryRegistry()
    registry.register("actor", "a-1", name="Goblin")

    sync = MagicMock()
    sync.pull_actor.return_value = ActorSnapshot(id="a-1", name="Goblin", hp=7)

    cr = MagicMock()
    cr.dispatch.return_value = MagicMock(ok=True)

    rec = Reconciler(
        sync=sync,
        registry=registry,
        event_bus=bus,
        command_router=cr,
        actor_state_provider=lambda aid: {"hp": 3, "name": "Goblin"} if aid == "a-1" else None,
    )
    diff = rec.run()
    assert "a-1" in diff.actors
    n = rec.auto_heal(diff)
    assert n == 1
    cr.dispatch.assert_called_once()
    # Event was published
    assert any(name == "sync.diff_detected" for name, _ in bus.history)


def test_reconciler_clean_state():
    registry = FoundryRegistry()
    registry.register("actor", "a-1", name="A")
    sync = MagicMock()
    sync.pull_actor.return_value = ActorSnapshot(id="a-1", name="A", hp=10)
    rec = Reconciler(
        sync=sync,
        registry=registry,
        actor_state_provider=lambda aid: {"hp": 10, "name": "A"},
    )
    diff = rec.run()
    assert diff.is_clean()


def test_socket_bridge_republishes_inbound_events():
    bus = EventBus()
    client = FoundryClient()  # never connected
    bridge = SocketBridge(client, bus)
    bridge.connect()

    received = []
    bus.subscribe("foundry.token_moved", received.append)

    # Simulate the relay receive loop pushing an event envelope.
    client._handle_incoming({  # noqa: SLF001 — test
        "type": "event",
        "event": "token_moved",
        "payload": {"token_id": "tok-1", "x": 100, "y": 200},
    })

    assert len(received) == 1
    assert received[0]["token_id"] == "tok-1"
    assert received[0]["origin"] == "foundry"

