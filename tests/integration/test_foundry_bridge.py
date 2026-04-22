from ai_dm.foundry.payloads import move_token
from ai_dm.foundry.protocol import build_command_envelope, is_valid_request_id


def test_move_token_payload():
    payload = move_token("t1", 10, 20)
    assert payload["type"] == "move_token"


def test_command_envelope_carries_request_id():
    env = build_command_envelope(move_token("t1", 1, 2))
    assert env["type"] == "command"
    assert is_valid_request_id(env["request_id"])
    assert env["command"]["type"] == "move_token"
