"""Unit tests for InteractionEffectsApplier."""
from __future__ import annotations

from unittest.mock import MagicMock

from ai_dm.orchestration.event_bus import EventBus
from ai_dm.orchestration.interaction_effects import InteractionEffectsApplier


def _setup(*, with_em=True):
    bus = EventBus()
    cr = MagicMock()
    cr.dispatch.return_value = MagicMock(ok=True)
    em = MagicMock() if with_em else None
    applier = InteractionEffectsApplier(
        event_bus=bus, command_router=cr, encounter_manager=em,
    )
    applier.start()
    return bus, cr, em, applier


def _payload(corr, *, success=True, request_id="r1", actor_id="hero", scene_id="s1"):
    return {
        "record": {
            "success": success,
            "request_id": request_id,
            "actor_id": actor_id,
            "scene_id": scene_id,
        },
        "correlation": corr,
    }


def test_grants_dispatched_as_give_item():
    bus, cr, _em, _ap = _setup()
    bus.publish("roll.resolved", _payload({"grants": ["loot.bowl", "loot.coin"]}))
    cr.dispatch.assert_called_once()
    cmds = list(cr.dispatch.call_args.args[0])
    assert [c.type for c in cmds] == ["give_item", "give_item"]
    assert [c.item_key for c in cmds] == ["loot.bowl", "loot.coin"]
    assert all(c.actor_id == "hero" for c in cmds)


def test_grants_string_form_accepted():
    bus, cr, _em, _ap = _setup()
    bus.publish("roll.resolved", _payload({"grants": "loot.lone"}))
    cmds = list(cr.dispatch.call_args.args[0])
    assert [c.item_key for c in cmds] == ["loot.lone"]


def test_starts_and_ends_encounter_invoked():
    bus, _cr, em, _ap = _setup()
    bus.publish("roll.resolved", _payload({"starts_encounter": "encounter.x"}))
    em.start_encounter.assert_called_once()
    args, kwargs = em.start_encounter.call_args
    assert args[0] == "encounter.x"
    assert kwargs.get("reason") == "authored_interaction"

    bus.publish("roll.resolved", _payload(
        {"ends_encounter": "encounter.x"}, request_id="r2",
    ))
    em.end_encounter.assert_called_once_with(
        "encounter.x", reason="authored_interaction",
    )


def test_ends_scene_dispatches_activate_and_publishes_entered():
    bus, cr, _em, _ap = _setup()
    seen: list[dict] = []
    bus.subscribe("scene.entered", seen.append)
    bus.publish("roll.resolved", _payload({"ends_scene": "exit.gate"}))
    cmds = list(cr.dispatch.call_args.args[0])
    assert cmds[0].type == "activate_scene"
    assert cmds[0].scene_id == "exit.gate"
    assert seen and seen[0]["scene_id"] == "exit.gate"
    assert seen[0]["from"] == "s1"


def test_failure_skips_all_hooks():
    bus, cr, em, _ap = _setup()
    bus.publish("roll.resolved", _payload(
        {"grants": ["x"], "starts_encounter": "e"}, success=False,
    ))
    cr.dispatch.assert_not_called()
    em.start_encounter.assert_not_called()


def test_duplicate_request_id_deduped():
    bus, cr, em, _ap = _setup()
    p = _payload({"starts_encounter": "encounter.x"}, request_id="dup")
    bus.publish("roll.resolved", p)
    bus.publish("roll.resolved", p)
    assert em.start_encounter.call_count == 1


def test_failed_grants_does_not_block_encounter():
    bus, cr, em, _ap = _setup()
    cr.dispatch.side_effect = RuntimeError("boom")
    bus.publish("roll.resolved", _payload({
        "grants": ["loot.x"], "starts_encounter": "encounter.x",
    }))
    em.start_encounter.assert_called_once()

