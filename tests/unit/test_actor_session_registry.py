from ai_dm.orchestration.actor_session import ActorSession, ActorSessionRegistry


class _StubPaths:
    def __init__(self, seed):
        self.characters_seed = seed


class _StubState:
    def __init__(self, characters):
        self.characters = characters


class _StubPack:
    def __init__(self, characters_dir, seed_dir):
        self.state = _StubState(characters_dir)
        self.paths = _StubPaths(seed_dir)


def test_get_or_create_idempotent(tmp_path):
    reg = ActorSessionRegistry(pack=None)
    a = reg.get_or_create("a-1", "Alice")
    b = reg.get_or_create("a-1", "Alice")
    assert a is b
    assert isinstance(a, ActorSession)
    assert a.actor_name == "Alice"
    assert a.character_sheet == {"id": "a-1", "name": "Alice"}


def test_loads_live_sheet_then_seed(tmp_path):
    chars = tmp_path / "live"
    seed = tmp_path / "seed"
    chars.mkdir()
    seed.mkdir()
    (seed / "a-1.json").write_text('{"id": "a-1", "name": "Seeded", "class": "fighter"}')
    pack = _StubPack(chars, seed)
    reg = ActorSessionRegistry(pack=pack)
    s = reg.get_or_create("a-1", "Override")
    # No live sheet → should pick up the seed
    assert s.character_sheet["class"] == "fighter"

    # Now write a live sheet for a-2 and verify precedence
    (chars / "a-2.json").write_text('{"id": "a-2", "name": "Live", "class": "wizard"}')
    (seed / "a-2.json").write_text('{"id": "a-2", "name": "Seeded", "class": "fighter"}')
    s2 = reg.get_or_create("a-2", None)
    assert s2.character_sheet["class"] == "wizard"


def test_user_fields_are_remembered():
    reg = ActorSessionRegistry(pack=None)
    s = reg.get_or_create("a-1", "Alice", user_id="u-1", user_name="Bob")
    assert s.user_id == "u-1"
    assert s.user_name == "Bob"
    # Subsequent call without user info preserves them.
    s2 = reg.get_or_create("a-1", "Alice")
    assert s2.user_id == "u-1"
    assert s2.user_name == "Bob"

