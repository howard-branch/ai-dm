import json
from pathlib import Path

from ai_dm.game.trigger_loader import load_triggers
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.orchestration.triggers import Trigger, TriggerEngine, trigger_from_spec
from ai_dm.orchestration.conditions import all_of, flag_eq, payload_eq
from ai_dm.orchestration.trigger_actions import publish_event, set_flag


def _engine_with_flags():
    bus = EventBus()
    flags: dict = {}
    engine = TriggerEngine(bus, context_provider=lambda: {"flags": flags})
    return bus, flags, engine


def test_basic_trigger_fires():
    bus, flags, engine = _engine_with_flags()
    engine.register(
        Trigger(
            id="t1",
            event="combat.encounter_ended",
            do=[set_flag(flags, "ended", True)],
        )
    )
    bus.publish("combat.encounter_ended", {"encounter_id": "e1"})
    assert flags["ended"] is True


def test_predicate_blocks_fire():
    bus, flags, engine = _engine_with_flags()
    engine.register(
        Trigger(
            id="t1",
            event="combat.encounter_ended",
            when=payload_eq("encounter_id", "e2"),
            do=[set_flag(flags, "fired", True)],
        )
    )
    bus.publish("combat.encounter_ended", {"encounter_id": "e1"})
    assert "fired" not in flags
    bus.publish("combat.encounter_ended", {"encounter_id": "e2"})
    assert flags["fired"] is True


def test_once_trigger_only_fires_once():
    bus, flags, engine = _engine_with_flags()
    counter = {"n": 0}

    def inc(_p, _c):
        counter["n"] += 1

    engine.register(Trigger(id="t1", event="x", do=[inc], once=True))
    bus.publish("x", {})
    bus.publish("x", {})
    bus.publish("x", {})
    assert counter["n"] == 1
    assert "t1" in engine.snapshot()


def test_compound_predicate():
    bus, flags, engine = _engine_with_flags()
    flags["boss_defeated"] = True
    engine.register(
        Trigger(
            id="t1",
            event="combat.encounter_ended",
            when=all_of(flag_eq("boss_defeated", True), payload_eq("encounter_id", "boss")),
            do=[set_flag(flags, "chapter_complete", True)],
        )
    )
    bus.publish("combat.encounter_ended", {"encounter_id": "boss"})
    assert flags["chapter_complete"] is True


def test_reentrancy_bounded():
    bus, _flags, engine = _engine_with_flags()
    counter = {"n": 0}

    def echo(_p, _c):
        counter["n"] += 1
        bus.publish("loop", {})

    engine.register(Trigger(id="loop", event="loop", do=[echo]))
    bus.publish("loop", {})
    # depth-bounded; should not blow stack
    assert counter["n"] <= 5


def test_action_failure_isolated():
    bus, flags, engine = _engine_with_flags()

    def boom(_p, _c):
        raise RuntimeError("nope")

    engine.register(
        Trigger(
            id="t1",
            event="x",
            do=[boom, set_flag(flags, "later", True)],
        )
    )
    bus.publish("x", {})
    assert flags["later"] is True
    assert any(n == "trigger.error" for n, _ in bus.history)


def test_snapshot_round_trip():
    bus, _flags, engine = _engine_with_flags()
    engine.register(Trigger(id="t1", event="x", do=[lambda p, c: None], once=True))
    bus.publish("x", {})
    snap = engine.snapshot()
    new_engine = TriggerEngine(bus)
    new_engine.restore(snap)
    assert new_engine.fired_once_ids == {"t1"}


def test_load_yaml_triggers(tmp_path: Path):
    bus = EventBus()
    flags: dict = {}
    spec = {
        "triggers": [
            {
                "id": "boss_complete",
                "event": "combat.encounter_ended",
                "when": {"payload_eq": {"encounter_id": "boss"}},
                "do": [
                    {"set_flag": {"key": "chapter_complete", "value": True}},
                    {"publish_event": {"name": "chapter.advanced"}},
                ],
                "once": True,
            }
        ]
    }
    p = tmp_path / "triggers.json"
    p.write_text(json.dumps(spec), encoding="utf-8")

    deps = {"event_bus": bus, "flags": flags}
    triggers = load_triggers(tmp_path, deps=deps)
    assert len(triggers) == 1

    engine = TriggerEngine(bus)
    engine.load(triggers)

    bus.publish("combat.encounter_ended", {"encounter_id": "boss"})
    assert flags["chapter_complete"] is True
    assert any(n == "chapter.advanced" for n, _ in bus.history)


def test_trigger_from_spec_smoke():
    bus = EventBus()
    flags: dict = {}
    deps = {"event_bus": bus, "flags": flags}
    t = trigger_from_spec(
        {"id": "x", "event": "y", "do": [{"set_flag": {"key": "k", "value": 1}}]},
        deps=deps,
    )
    assert t.id == "x"

