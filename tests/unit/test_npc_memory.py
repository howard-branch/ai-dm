from ai_dm.memory.npc_memory import MemoryEvent, NPCMemoryStore


def test_record_and_recent():
    store = NPCMemoryStore(max_events_per_npc=5)
    store.record("g1", MemoryEvent(text="saw the party", tags=["sight"]))
    store.record("g1", MemoryEvent(kind="fact", text="fears fire"))
    assert [e.text for e in store.recent("g1")] == ["saw the party"]
    assert [e.text for e in store.facts("g1")] == ["fears fire"]


def test_summariser_called_on_overflow():
    summarised: list[list[MemoryEvent]] = []

    def summariser(events):
        summarised.append(list(events))
        return f"({len(events)} dropped)"

    store = NPCMemoryStore(max_events_per_npc=2, summariser=summariser)
    for i in range(5):
        store.record("g1", MemoryEvent(text=f"e{i}"))
    assert summarised  # was called
    assert "dropped" in store.summary("g1")
    assert len(store.recent("g1", n=10)) == 2  # bounded


def test_snapshot_round_trip():
    store = NPCMemoryStore()
    store.record("g1", MemoryEvent(text="x"))
    other = NPCMemoryStore()
    other.restore(store.snapshot())
    assert other.recent("g1")[0].text == "x"

