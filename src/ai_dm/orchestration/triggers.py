"""Trigger engine: declarative reactions over the EventBus.

A :class:`Trigger` listens for one event name, evaluates a predicate
against the payload + a snapshot of runtime ``context`` (flags, chapter,
combat actors, …), then runs an ordered list of actions. ``once=True``
triggers fire at most once per save (their ids are persisted in
``CampaignSnapshot.fired_once_ids``).

Reentrancy is bounded: if action ``A`` publishes the same event that
fired it, the depth counter prevents an infinite loop.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from ai_dm.orchestration.conditions import Predicate, always, from_spec as cond_from_spec
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.orchestration.trigger_actions import Action, from_spec as action_from_spec

logger = logging.getLogger("ai_dm.triggers")

ContextProvider = Callable[[], dict]
_MAX_REENTRY_DEPTH = 4


@dataclass
class Trigger:
    id: str
    event: str
    when: Predicate = field(default_factory=always)
    do: list[Action] = field(default_factory=list)
    once: bool = False
    priority: int = 0


class TriggerEngine:
    def __init__(
        self,
        bus: EventBus,
        *,
        context_provider: ContextProvider | None = None,
    ) -> None:
        self.bus = bus
        self.context_provider = context_provider or (lambda: {})
        self._lock = threading.RLock()
        self._triggers: dict[str, list[Trigger]] = defaultdict(list)
        self._unsubs: list[Callable[[], None]] = []
        self.fired_once_ids: set[str] = set()
        self._depth = threading.local()

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #

    def register(self, trigger: Trigger) -> None:
        with self._lock:
            self._triggers[trigger.event].append(trigger)
            self._triggers[trigger.event].sort(key=lambda t: -t.priority)
            if not any(getattr(u, "_event", None) == trigger.event for u in self._unsubs):
                unsub = self.bus.subscribe(trigger.event, self._make_handler(trigger.event))
                # tag so we don't double-subscribe
                setattr(unsub, "_event", trigger.event)
                self._unsubs.append(unsub)

    def load(self, triggers: list[Trigger]) -> None:
        for t in triggers:
            self.register(t)

    def shutdown(self) -> None:
        with self._lock:
            for u in self._unsubs:
                try:
                    u()
                except Exception:  # noqa: BLE001
                    pass
            self._unsubs.clear()
            self._triggers.clear()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def snapshot(self) -> list[str]:
        with self._lock:
            return sorted(self.fired_once_ids)

    def restore(self, ids: list[str] | None) -> None:
        with self._lock:
            self.fired_once_ids = set(ids or [])

    # ------------------------------------------------------------------ #
    # Test helper
    # ------------------------------------------------------------------ #

    def fire_manual(self, event: str, payload: dict) -> None:
        self._make_handler(event)(payload)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _make_handler(self, event: str):
        def _handler(payload: dict) -> None:
            depth = getattr(self._depth, "n", 0)
            if depth >= _MAX_REENTRY_DEPTH:
                logger.warning("trigger reentry depth exceeded for %s", event)
                return
            self._depth.n = depth + 1
            try:
                self._process(event, payload)
            finally:
                self._depth.n = depth

        return _handler

    def _process(self, event: str, payload: dict) -> None:
        with self._lock:
            triggers = list(self._triggers.get(event, ()))
        ctx = self._context()
        for trig in triggers:
            if trig.once and trig.id in self.fired_once_ids:
                continue
            try:
                if not trig.when(payload, ctx):
                    continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("trigger %s predicate failed: %s", trig.id, exc)
                continue
            self._fire(trig, payload, ctx)

    def _fire(self, trig: Trigger, payload: dict, ctx: dict) -> None:
        for action in trig.do:
            try:
                action(payload, ctx)
            except Exception as exc:  # noqa: BLE001
                logger.warning("trigger %s action failed: %s", trig.id, exc)
                self.bus.publish(
                    "trigger.error",
                    {"trigger_id": trig.id, "error": str(exc)},
                )
        if trig.once:
            self.fired_once_ids.add(trig.id)
        self.bus.publish("trigger.fired", {"trigger_id": trig.id, "event": trig.event})

    def _context(self) -> dict:
        try:
            return self.context_provider() or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("trigger context provider failed: %s", exc)
            return {}


# ---------------------------------------------------------------------- #
# Spec loader
# ---------------------------------------------------------------------- #

def trigger_from_spec(spec: dict, *, deps: dict) -> Trigger:
    """Build a :class:`Trigger` from a YAML/JSON spec.

    Spec shape::

        id: noticed_door_advance
        event: combat.encounter_ended
        once: true
        priority: 10
        when:
          all_of:
            - flag_eq: {boss_defeated: true}
            - chapter_is: chapter_01
        do:
          - set_flag: {key: chapter_complete, value: true}
          - publish_event: {name: chapter.advanced}
    """
    return Trigger(
        id=spec["id"],
        event=spec["event"],
        when=cond_from_spec(spec.get("when", True)),
        do=[action_from_spec(a, deps=deps) for a in spec.get("do", [])],
        once=bool(spec.get("once", False)),
        priority=int(spec.get("priority", 0)),
    )

