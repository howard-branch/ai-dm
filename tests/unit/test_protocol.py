from ai_dm.foundry.protocol import (
    build_batch_envelope,
    build_command_envelope,
    is_valid_request_id,
    new_request_id,
)


def test_request_id_format():
    rid = new_request_id()
    assert rid.startswith("req-")
    assert len(rid) > len("req-")
    assert is_valid_request_id(rid)
    assert not is_valid_request_id("")
    assert not is_valid_request_id(None)


def test_command_envelope_shape():
    env = build_command_envelope({"type": "ping"}, correlation_id="turn-1")
    assert env["type"] == "command"
    assert env["command"] == {"type": "ping"}
    assert env["correlation_id"] == "turn-1"
    assert is_valid_request_id(env["request_id"])
    assert "issued_at" in env


def test_batch_envelope_shape():
    env = build_batch_envelope([{"type": "a"}, {"type": "b"}])
    assert env["type"] == "batch"
    assert env["commands"] == [{"type": "a"}, {"type": "b"}]
    assert "correlation_id" not in env

