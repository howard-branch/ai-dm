from ai_dm.ai.context_builder import PromptContextBuilder
from ai_dm.memory.npc_memory import MemoryEvent, NPCMemoryStore
from ai_dm.memory.relationships import RelationshipMatrix


class _State:
    def get_context(self):
        return {"flag": True}


def test_focus_npcs_appear_in_context():
    mem = NPCMemoryStore()
    mem.record("g1", MemoryEvent(text="ate goat"))
    rels = RelationshipMatrix()
    rels.set("g1", "morgana", -20)

    builder = PromptContextBuilder(_State(), mem, rels, None)
    ctx = builder.build("hello", focus_npcs=["g1"])
    assert ctx["state"] == {"flag": True}
    briefs = ctx["npc_briefs"]
    assert briefs[0]["npc_id"] == "g1"
    assert briefs[0]["recent_events"][0]["text"] == "ate goat"
    assert briefs[0]["relationships"][0]["target"] == "morgana"


def test_no_npc_briefs_without_focus():
    builder = PromptContextBuilder(_State(), NPCMemoryStore(), RelationshipMatrix(), None)
    ctx = builder.build("hi")
    assert "npc_briefs" not in ctx

