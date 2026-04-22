from ai_dm.game.state_store import StateStore


def test_state_store_context_has_expected_keys():
    store = StateStore()
    context = store.get_context()
    assert "session" in context
    assert "world" in context
    assert "campaign" in context
