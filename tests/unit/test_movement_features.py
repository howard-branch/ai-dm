"""Tests for the new movement features:

* Partial / directional intent parsing ("move 30 ft toward altar").
* LocationService geometry helpers (point_along, formation_slots).
* IntentRouter party-move formation slots + clock advancement.
* movement_time helper.
"""
from __future__ import annotations

import random
from unittest.mock import MagicMock

from ai_dm.ai.intent_parser import IntentParser
from ai_dm.ai.intent_router import IntentRouter
from ai_dm.ai.intent_schemas import PlayerIntent
from ai_dm.game.clock import Clock
from ai_dm.game.location_model import Anchor, SceneLocation
from ai_dm.game.location_service import LocationService
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.rules.action_resolver import ActionResolver
from ai_dm.rules.engine import RulesEngine
from ai_dm.rules.movement_time import in_scene_move_minutes, travel_minutes


# ---------------------------------------------------------------------- #
# IntentParser
# ---------------------------------------------------------------------- #

def test_parser_partial_move_toward_anchor():
    p = IntentParser()
    out = p.parse("move 30 feet toward the altar")
    assert out.type == "move"
    assert out.target_anchor == "altar"
    assert out.distance_ft == 30
    assert out.direction == "toward"


def test_parser_partial_move_cardinal_only():
    p = IntentParser()
    out = p.parse("advance 15 ft north")
    assert out.type == "move"
    assert out.distance_ft == 15
    assert out.direction == "north"
    # No anchor — pure cardinal projection.
    assert out.target_anchor is None


def test_parser_partial_move_back_from_target():
    p = IntentParser()
    out = p.parse("step back 10 feet from the goblin")
    assert out.type == "move"
    assert out.distance_ft == 10
    assert out.direction == "away"
    assert out.target_anchor == "goblin"


def test_parser_plain_move_unaffected():
    p = IntentParser()
    out = p.parse("I move to the altar")
    assert out.type == "move"
    assert out.target_anchor == "altar"
    assert out.distance_ft is None
    assert out.direction is None


# ---------------------------------------------------------------------- #
# LocationService geometry
# ---------------------------------------------------------------------- #

def _scene() -> SceneLocation:
    return SceneLocation(
        scene_id="s1",
        anchors=[Anchor(id="a", name="altar", scene_id="s1", x=400, y=400)],
        grid_size_px=100,
        feet_per_grid=5,
    )


def test_pixels_per_foot_uses_scene_grid():
    svc = LocationService()
    svc.load_scene(_scene())
    assert svc.pixels_per_foot("s1") == 20.0  # 100 px / 5 ft


def test_point_along_clamps_at_target_endpoint():
    svc = LocationService()
    svc.load_scene(_scene())
    # 30 ft @ 20 px/ft = 600 px; segment length is sqrt((400-100)^2 + 0) = 300.
    # Should clamp to the endpoint.
    p = svc.point_along("s1", (100, 400), (400, 400), 30)
    assert p == (400, 400)


def test_point_along_partial_distance():
    svc = LocationService()
    svc.load_scene(_scene())
    # 5 ft = 100 px along x-axis from (100, 400) toward (700, 400).
    p = svc.point_along("s1", (100, 400), (700, 400), 5)
    assert p == (200, 400)  # snapped to grid


def test_formation_offsets_unique_for_party_of_4():
    offs = LocationService.formation_offsets(4)
    assert len(offs) == 4
    assert len(set(offs)) == 4  # all distinct


def test_formation_slots_around_centre_are_distinct():
    svc = LocationService()
    svc.load_scene(_scene())
    slots = svc.formation_slots("s1", (400, 400), 4)
    assert len(slots) == 4
    assert len(set(slots)) == 4
    # First slot is the centre, snapped to grid.
    assert slots[0] == (400, 400)


def test_project_direction_north_subtracts_y():
    svc = LocationService()
    svc.load_scene(_scene())
    # 10 ft north @ 20 px/ft = 200 px upward.
    p = svc.project_direction("s1", (500, 500), "north", 10)
    assert p == (500, 300)


# ---------------------------------------------------------------------- #
# movement_time
# ---------------------------------------------------------------------- #

def test_in_combat_move_costs_zero_minutes():
    assert in_scene_move_minutes(30, in_combat=True) == 0


def test_out_of_combat_move_always_at_least_one_minute():
    # 30 ft normal pace = 0.1 min ≈ rounds up to 1.
    assert in_scene_move_minutes(30) == 1
    assert in_scene_move_minutes(None) == 1


def test_long_move_scales_by_pace():
    # 600 ft @ normal pace (300 ft/min) = 2 min.
    assert in_scene_move_minutes(600) == 2


def test_travel_minutes_falls_back_to_default_with_no_distance():
    assert travel_minutes(None) == 30
    assert travel_minutes(0) == 30


def test_travel_minutes_scales_with_distance():
    # 3 mi @ 3 mph normal pace = 1 hour = 60 min.
    assert travel_minutes(3.0) == 60


# ---------------------------------------------------------------------- #
# IntentRouter — partial moves and party formation tick the clock
# ---------------------------------------------------------------------- #

def _router_with_clock(*, party_ids: list[str] | None = None):
    rules = RulesEngine(rng=random.Random(1))
    resolver = ActionResolver(rules=rules)
    cr = MagicMock()
    cr.dispatch.return_value = MagicMock(ok=True, results=[])
    bus = EventBus()
    clock = Clock(event_bus=bus)
    router = IntentRouter(
        action_resolver=resolver,
        command_router=cr,
        event_bus=bus,
        default_scene_id="s1",
        clock=clock,
    )
    if party_ids is not None:
        # Bypass the pack lookup: stub the helper to return our list.
        router._party_member_ids = lambda lead, _ids=party_ids: list(_ids)  # type: ignore[assignment]
    return router, cr, clock, bus


def test_single_move_advances_clock_at_least_one_minute():
    router, cr, clock, _ = _router_with_clock()
    intent = PlayerIntent(
        type="move", actor_id="hero", target_anchor="altar",
        distance_ft=30, direction="toward", raw_text="move 30 ft toward altar",
    )
    env = router.handle(intent)
    assert env.commands_ok is True
    assert clock.total_minutes >= 1
    # Distance + direction reach the dispatched command unchanged.
    sent = cr.dispatch.call_args.args[0][0]
    assert sent.distance_ft == 30
    assert sent.direction == "toward"


def test_party_move_assigns_distinct_formation_slots():
    router, cr, _, _ = _router_with_clock(party_ids=["lead", "a", "b", "c"])
    # Stub per-member result list so _dispatch_party_move's success
    # accounting matches the dispatched command count.
    cr.dispatch.return_value = MagicMock(
        ok=True,
        results=[MagicMock(ok=True, error=None) for _ in range(4)],
    )
    intent = PlayerIntent(
        type="move", actor_id="lead", target_anchor="altar",
        raw_text="party to altar", extra={"party_scope": True},
    )
    env = router.handle(intent)
    assert env.commands_ok is True
    sent = cr.dispatch.call_args.args[0]
    assert len(sent) == 4
    indexes = sorted(c.formation_index for c in sent)
    assert indexes == [0, 1, 2, 3]
    counts = {c.formation_count for c in sent}
    assert counts == {4}


def test_combat_move_does_not_advance_clock():
    router, _, clock, _ = _router_with_clock()
    intent = PlayerIntent(
        type="move", actor_id="hero", target_anchor="goblin",
        distance_ft=30, raw_text="move 30 ft toward goblin",
    )
    router.handle(intent, ctx={"in_combat": True})
    assert clock.total_minutes == 0

