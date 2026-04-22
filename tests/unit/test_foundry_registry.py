import pytest

from ai_dm.foundry.errors import RegistryMissError
from ai_dm.foundry.registry import FoundryRegistry


def test_register_and_resolve_scene():
    reg = FoundryRegistry()
    reg.register("scene", "scene-abc", name="Candlekeep", aliases=("Library",))
    assert reg.resolve("scene", "scene-abc") == "scene-abc"
    assert reg.resolve("scene", "Candlekeep") == "scene-abc"
    assert reg.resolve("scene", "library") == "scene-abc"  # case-insensitive


def test_resolve_unknown_raises():
    reg = FoundryRegistry()
    with pytest.raises(RegistryMissError):
        reg.resolve("actor", "ghost")


def test_token_scope_collision_allowed_across_scenes():
    reg = FoundryRegistry()
    reg.register("scene", "s1", name="One")
    reg.register("scene", "s2", name="Two")
    reg.register("token", "t1", name="goblin", scene_id="s1")
    reg.register("token", "t2", name="goblin", scene_id="s2")
    assert reg.resolve("token", "goblin", scene_id="s1") == "t1"
    assert reg.resolve("token", "goblin", scene_id="s2") == "t2"


def test_token_alias_collision_within_scene_rejected():
    reg = FoundryRegistry()
    reg.register("token", "t1", name="goblin", scene_id="s1")
    with pytest.raises(ValueError):
        reg.register("token", "t2", name="goblin", scene_id="s1")


def test_unregister_clears_aliases():
    reg = FoundryRegistry()
    reg.register("actor", "a1", name="Morgana")
    reg.unregister("actor", "a1")
    with pytest.raises(RegistryMissError):
        reg.resolve("actor", "Morgana")

