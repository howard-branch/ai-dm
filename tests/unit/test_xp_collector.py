"""Unit tests for the combat XP collector."""
from __future__ import annotations

import random

from ai_dm.game.combat_machine import CombatMachine
from ai_dm.game.combat_state import Participant
from ai_dm.game.party_state import PartyState
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.orchestration.xp_collector import XPCollector


def _bus_combat() -> tuple[EventBus, CombatMachine, PartyState, XPCollector, list]:
    bus = EventBus()
    combat = CombatMachine(event_bus=bus, rng=random.Random(0))
    ps = PartyState()
    seen: list[dict] = []
    bus.subscribe("narrator.output_ready", seen.append)
    xp = XPCollector(event_bus=bus, combat=combat, party_state=ps)
    xp.start()
    return bus, combat, ps, xp, seen


def _pc(actor_id: str = "pc1", name: str = "Hero") -> Participant:
    return Participant.model_validate({
        "actor_id": actor_id, "name": name, "team": "party",
        "hp": 20, "max_hp": 20,
    })


def _foe(actor_id: str, *, xp: int, cr: float = 1.0, key: str = "goblin") -> Participant:
    return Participant.model_validate({
        "actor_id": actor_id, "name": actor_id, "team": "foe",
        "hp": 7, "max_hp": 7,
        "xp": xp, "challenge_rating": cr, "stat_block_key": key,
        "source": "npc",
    })


def test_kill_credits_xp_to_party_on_encounter_end():
    bus, combat, ps, xp, seen = _bus_combat()
    combat.start_encounter("e1", [_pc(), _foe("g1", xp=100), _foe("g2", xp=100)])

    bus.publish("rules.damage_applied", {
        "target_id": "g1", "amount": 10, "damage_type": "slashing",
        "outcome": {"dropped_to_zero": True},
    })
    bus.publish("rules.damage_applied", {
        "target_id": "g2", "amount": 10, "damage_type": "slashing",
        "outcome": {"dropped_to_zero": True},
    })
    # Foundry-side double-tap on g1: same encounter, same actor → no
    # second bank.
    bus.publish("rules.damage_applied", {
        "target_id": "g1", "amount": 5,
        "outcome": {"dropped_to_zero": True},
    })
    assert ps.pending_xp == 200  # 100 + 100, the third was deduped

    combat.end_encounter("victory")

    assert ps.pending_xp == 0
    assert ps.xp_pool["pc1"] == 200
    # Narrator line was published.
    assert any("XP each" in s.get("narration", "") for s in seen)


def test_pc_drop_does_not_award_xp():
    bus, combat, ps, xp, _seen = _bus_combat()
    combat.start_encounter("e1", [_pc("pc1"), _foe("g1", xp=50)])

    # PC goes down — must NOT bank xp.
    bus.publish("rules.damage_applied", {
        "target_id": "pc1", "amount": 99,
        "outcome": {"dropped_to_zero": True},
    })
    assert ps.pending_xp == 0


def test_damage_outside_encounter_is_ignored():
    bus, _combat, ps, _xp, _seen = _bus_combat()
    bus.publish("rules.damage_applied", {
        "target_id": "g1", "outcome": {"dropped_to_zero": True},
    })
    assert ps.pending_xp == 0


def test_zero_xp_kill_skipped():
    bus, combat, ps, _xp, seen = _bus_combat()
    combat.start_encounter("e1", [_pc(), _foe("vermin", xp=0)])
    bus.publish("rules.damage_applied", {
        "target_id": "vermin", "outcome": {"dropped_to_zero": True},
    })
    combat.end_encounter()
    assert ps.xp_pool.get("pc1", 0) == 0
    assert seen == []


def test_level_up_pending_announced():
    bus, combat, ps, _xp, seen = _bus_combat()
    combat.start_encounter("e1", [_pc(), _foe("dragon", xp=400)])
    # 400 xp pushes a level-1 PC past the 300 threshold for level 2.
    bus.publish("rules.damage_applied", {
        "target_id": "dragon", "outcome": {"dropped_to_zero": True},
    })
    combat.end_encounter()
    assert ps.xp_pool["pc1"] == 400
    text = "\n".join(s.get("narration", "") for s in seen)
    assert "pc1 reached level 2" in text

