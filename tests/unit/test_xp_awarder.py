"""Unit tests for the story / interaction XPAwarder."""
from __future__ import annotations

from ai_dm.game.party_state import PartyState
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.orchestration.xp_awarder import XPAwarder


def _setup(members=("pc1", "pc2")):
    bus = EventBus()
    ps = PartyState()
    for m in members:
        ps.add_member(m)
    seen: list[dict] = []
    bus.subscribe("narrator.output_ready", seen.append)
    awarder = XPAwarder(event_bus=bus, party_state=ps)
    awarder.start()
    return bus, ps, awarder, seen


def _resolved(*, success=True, xp=50, request_id="r1", actor_id="pc1"):
    return {
        "record": {
            "success": success,
            "request_id": request_id,
            "actor_id": actor_id,
            "scene_id": "s1",
        },
        "correlation": {"xp": xp, "feature": "Ruined Altar"},
    }


def test_award_on_success_splits_xp_evenly():
    bus, ps, _aw, seen = _setup()
    bus.publish("roll.resolved", _resolved(xp=50))
    assert ps.xp_pool["pc1"] == 25
    assert ps.xp_pool["pc2"] == 25
    assert any("story XP" in s.get("narration", "") for s in seen)


def test_failure_does_not_award():
    bus, ps, _aw, _seen = _setup()
    bus.publish("roll.resolved", _resolved(success=False))
    assert ps.xp_pool["pc1"] == 0


def test_no_xp_field_no_award():
    bus, ps, _aw, _seen = _setup()
    bus.publish("roll.resolved", {
        "record": {"success": True, "request_id": "r2", "actor_id": "pc1"},
        "correlation": {},
    })
    assert ps.xp_pool["pc1"] == 0


def test_duplicate_request_id_deduped():
    bus, ps, _aw, _seen = _setup()
    bus.publish("roll.resolved", _resolved(xp=50, request_id="dup"))
    bus.publish("roll.resolved", _resolved(xp=50, request_id="dup"))
    assert ps.xp_pool["pc1"] == 25  # not 50


def test_lazy_registers_actor_when_party_empty():
    bus = EventBus()
    ps = PartyState()  # no members
    XPAwarder(event_bus=bus, party_state=ps).start()
    bus.publish("roll.resolved", _resolved(xp=20, actor_id="hero"))
    assert "hero" in ps.members
    assert ps.xp_pool["hero"] == 20


def test_level_up_announced():
    bus, ps, _aw, seen = _setup(members=("pc1",))
    bus.publish("roll.resolved", _resolved(xp=400))
    text = "\n".join(s.get("narration", "") for s in seen)
    assert "pc1 reached level 2" in text

