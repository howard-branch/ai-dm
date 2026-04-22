from ai_dm.orchestration.event_bus import EventBus


def test_subscribe_and_publish():
    bus = EventBus()
    received: list[dict] = []
    bus.subscribe("foo", lambda p: received.append(p))
    bus.publish("foo", {"x": 1})
    assert received == [{"x": 1}]


def test_unsubscribe():
    bus = EventBus()
    received = []
    unsub = bus.subscribe("foo", lambda p: received.append(p))
    unsub()
    bus.publish("foo", {})
    assert received == []


def test_failing_handler_does_not_break_others():
    bus = EventBus()
    bus.subscribe("foo", lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    fired = []
    bus.subscribe("foo", lambda p: fired.append(p))
    bus.publish("foo", {"y": 2})
    assert fired == [{"y": 2}]


def test_history_recorded():
    bus = EventBus()
    bus.publish("a", {"i": 1})
    bus.publish("b", {"i": 2})
    assert bus.history[-2:] == [("a", {"i": 1}), ("b", {"i": 2})]

