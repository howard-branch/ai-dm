import pytest

from ai_dm.foundry.errors import ValidationError
from ai_dm.foundry.registry import FoundryRegistry
from ai_dm.foundry.validator import CommandValidator
from ai_dm.models.commands import MoveTokenCommand, UpdateActorCommand


@pytest.fixture
def registry():
    reg = FoundryRegistry()
    reg.register("scene", "scene-1", name="Candlekeep")
    reg.register("actor", "actor-1", name="Morgana")
    reg.register("token", "token-1", name="goblin", scene_id="scene-1")
    return reg


def test_validator_resolves_token_alias(registry):
    v = CommandValidator(registry)
    cmd = v.validate({"type": "move_token", "token_id": "goblin", "x": 100, "y": 50, "scene_id": "scene-1"})
    assert isinstance(cmd, MoveTokenCommand)
    assert cmd.token_id == "token-1"


def test_validator_rejects_unknown_token(registry):
    v = CommandValidator(registry)
    with pytest.raises(ValidationError) as exc:
        v.validate({"type": "move_token", "token_id": "ghost", "x": 0, "y": 0, "scene_id": "scene-1"})
    assert exc.value.code == "unknown_token"


def test_validator_rejects_negative_coords(registry):
    v = CommandValidator(registry)
    with pytest.raises(ValidationError) as exc:
        v.validate({"type": "move_token", "token_id": "goblin", "x": -1, "y": 0, "scene_id": "scene-1"})
    assert exc.value.code == "bad_coordinates"


def test_validator_rejects_forbidden_patch_key(registry):
    v = CommandValidator(registry)
    with pytest.raises(ValidationError) as exc:
        v.validate({"type": "update_actor", "actor_id": "Morgana", "patch": {"flags.evil": True}})
    assert exc.value.code == "forbidden_patch_key"


def test_validator_allows_whitelisted_patch_key(registry):
    v = CommandValidator(registry)
    cmd = v.validate({"type": "update_actor", "actor_id": "Morgana", "patch": {"name": "M."}})
    assert isinstance(cmd, UpdateActorCommand)
    assert cmd.actor_id == "actor-1"


def test_validator_schema_failure_raises():
    v = CommandValidator(FoundryRegistry())
    with pytest.raises(ValidationError) as exc:
        v.validate({"type": "move_token", "token_id": "x"})  # missing x/y
    assert exc.value.code == "schema"

