import threading

from ai_dm.foundry.client import FoundryClient
from ai_dm.foundry.protocol import build_command_envelope


def test_handle_incoming_correlates_request():
    client = FoundryClient()
    env = build_command_envelope({"type": "ping"})
    rid = env["request_id"]

    # Manually register a pending request as if `request()` was called.
    from ai_dm.foundry.client import PendingRequest

    pending = PendingRequest(
        event=threading.Event(),
        command_type="ping",
        issued_at=0.0,
    )
    client._pending[rid] = pending

    client._inject_result({"type": "result", "request_id": rid, "result": {"ok": True}})

    assert pending.event.is_set()
    assert pending.response is not None
    assert pending.response["result"]["ok"] is True
    assert client.stats.received == 1


def test_orphan_results_tracked_not_raised():
    client = FoundryClient()
    client._inject_result({"type": "result", "request_id": "req-unknown", "result": {"ok": True}})
    assert client.stats.orphans == 1


def test_duplicate_results_tracked():
    client = FoundryClient()
    from ai_dm.foundry.client import PendingRequest

    pending = PendingRequest(event=threading.Event(), command_type="ping", issued_at=0.0)
    client._pending["req-x"] = pending
    client._inject_result({"type": "result", "request_id": "req-x", "result": {"ok": True}})
    # second time the same id should now be a duplicate
    client._inject_result({"type": "result", "request_id": "req-x", "result": {"ok": True}})
    assert client.stats.duplicates == 1


def test_missing_request_id_ignored():
    client = FoundryClient()
    client._inject_result({"type": "result", "result": {"ok": True}})
    assert client.stats.received == 0

