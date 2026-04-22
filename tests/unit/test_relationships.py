from ai_dm.memory.relationships import RelationshipMatrix


def test_set_and_get():
    m = RelationshipMatrix()
    m.set("goblin", "morgana", -40, tags=["betrayed"], notes="caught lying")
    rel = m.get("goblin", "morgana")
    assert rel is not None
    assert rel.disposition == -40
    assert "betrayed" in rel.tags


def test_adjust_clamps_to_range():
    m = RelationshipMatrix()
    m.adjust("g", "p", -200)
    assert m.get("g", "p").disposition == -100
    m.adjust("g", "p", +500)
    assert m.get("g", "p").disposition == 100


def test_for_subject_filters():
    m = RelationshipMatrix()
    m.set("g", "p1", 10)
    m.set("g", "p2", -10)
    m.set("h", "p1", 0)
    rels = m.for_subject("g")
    assert {r.target for r in rels} == {"p1", "p2"}


def test_snapshot_round_trip():
    m = RelationshipMatrix()
    m.set("g", "p", 50, tags=["friend"])
    other = RelationshipMatrix()
    other.restore(m.snapshot())
    assert other.get("g", "p").disposition == 50
    assert other.get("g", "p").tags == ["friend"]

