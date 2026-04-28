"""In-game clock and time-of-day phase tracker.

The engine had no notion of time before this module existed: random
encounters, rests and night-only events all had to piggy-back on
``scene.entered`` because nothing else ever fired. The :class:`Clock`
lives next to the EventBus, exposes ``advance(minutes, …)``, and emits
structured events whenever time crosses a boundary the rest of the
engine cares about:

* ``time.advanced``        — every ``advance()`` call.
* ``watch.passed``         — every full 4-hour watch boundary crossed.
* ``time.phase_changed``   — when day→dusk→night→dawn transitions.
* ``rest.short.completed`` — advance(>=60) with ``reason="short_rest"``.
* ``rest.long.completed``  — advance(>=480) with ``reason="long_rest"``.

Pack triggers can predicate on these events. The clock is seedable for
deterministic tests, snapshots a single ``total_minutes`` integer, and
keeps no other state.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger("ai_dm.game.clock")

Phase = Literal["dawn", "day", "dusk", "night"]

# Phase boundaries (24h clock, in minutes since midnight).
_DAWN_START = 5 * 60        # 05:00
_DAY_START  = 7 * 60        # 07:00
_DUSK_START = 18 * 60       # 18:00
_NIGHT_START = 20 * 60      # 20:00

_MINUTES_PER_HOUR = 60
_MINUTES_PER_DAY = 24 * _MINUTES_PER_HOUR
_MINUTES_PER_WATCH = 4 * _MINUTES_PER_HOUR      # 240

# Convenience for callers who don't want magic numbers.
SHORT_REST_MIN = 60
LONG_REST_MIN = 8 * 60


def _phase_for(minute_of_day: int) -> Phase:
    if _DAWN_START <= minute_of_day < _DAY_START:
        return "dawn"
    if _DAY_START <= minute_of_day < _DUSK_START:
        return "day"
    if _DUSK_START <= minute_of_day < _NIGHT_START:
        return "dusk"
    return "night"


@dataclass
class Clock:
    """Monotonic in-game clock.

    Time starts at day 1, 08:00 by default (so packs that do nothing
    open in daylight). Override ``start_minute_of_day`` if a campaign
    wants a different opener. ``total_minutes`` is the only state that
    matters — everything else is derived.
    """

    event_bus: Any | None = None
    start_minute_of_day: int = 8 * 60  # 08:00 day 1
    total_minutes: int = 0  # absolute minutes elapsed since campaign t=0

    # ------------------------------------------------------------------ #
    # Derived properties
    # ------------------------------------------------------------------ #

    @property
    def absolute_minute(self) -> int:
        """Minutes since campaign t=0 plus the offset opener."""
        return self.start_minute_of_day + self.total_minutes

    @property
    def day(self) -> int:
        """1-based day counter."""
        return 1 + (self.absolute_minute // _MINUTES_PER_DAY)

    @property
    def minute_of_day(self) -> int:
        return self.absolute_minute % _MINUTES_PER_DAY

    @property
    def hour_of_day(self) -> int:
        return self.minute_of_day // _MINUTES_PER_HOUR

    @property
    def phase(self) -> Phase:
        return _phase_for(self.minute_of_day)

    @property
    def hh_mm(self) -> str:
        return f"{self.hour_of_day:02d}:{self.minute_of_day % 60:02d}"

    # ------------------------------------------------------------------ #
    # Mutation
    # ------------------------------------------------------------------ #

    def advance(
        self,
        minutes: int,
        *,
        reason: str | None = None,
        scene_id: str | None = None,
    ) -> dict:
        """Move the clock forward by ``minutes`` and publish events.

        Returns a dict describing the change (handy for tests + logs).
        """
        if minutes <= 0:
            return self._state(delta=0, reason=reason, scene_id=scene_id)

        before_phase = self.phase
        before_watch = self.absolute_minute // _MINUTES_PER_WATCH
        before_day = self.day

        self.total_minutes += int(minutes)

        after_phase = self.phase
        after_watch = self.absolute_minute // _MINUTES_PER_WATCH
        after_day = self.day

        state = self._state(delta=int(minutes), reason=reason, scene_id=scene_id)
        logger.info(
            "clock advanced +%dm -> day %d %s phase=%s reason=%s scene=%s",
            minutes, after_day, self.hh_mm, after_phase, reason, scene_id,
            extra={"clock_advance": state},
        )

        if self.event_bus is None:
            return state

        # Always emit time.advanced so generic listeners get every tick.
        self._publish("time.advanced", state)

        # Watch boundaries (one event per crossing — a single 12h advance
        # emits three, so listeners see every wandering check).
        for w in range(before_watch + 1, after_watch + 1):
            payload = {
                **state,
                "watch_index": int(w),
                "minute_at_boundary": int(w * _MINUTES_PER_WATCH),
            }
            self._publish("watch.passed", payload)

        # Phase transition (collapsed to a single event regardless of
        # how many phases were skipped — listeners care about the
        # *current* phase, not every intermediate one).
        if after_phase != before_phase:
            self._publish("time.phase_changed", {
                **state,
                "from_phase": before_phase,
                "to_phase": after_phase,
            })

        # Day rollover.
        if after_day != before_day:
            self._publish("time.day_rolled", {
                **state,
                "from_day": before_day,
                "to_day": after_day,
            })

        # Rest convenience events: we don't enforce duration here, the
        # caller specifies the reason. If a pack issues
        # ``advance(60, reason="short_rest")`` we surface it as a
        # ``rest.short.completed`` so wandering checks can react.
        if reason == "short_rest":
            self._publish("rest.short.completed", state)
        elif reason == "long_rest":
            self._publish("rest.long.completed", state)

        return state

    # Convenience wrappers mirroring rules/rests.py.
    def short_rest(self, *, scene_id: str | None = None) -> dict:
        return self.advance(SHORT_REST_MIN, reason="short_rest", scene_id=scene_id)

    def long_rest(self, *, scene_id: str | None = None) -> dict:
        return self.advance(LONG_REST_MIN, reason="long_rest", scene_id=scene_id)

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict:
        return {
            "total_minutes": int(self.total_minutes),
            "start_minute_of_day": int(self.start_minute_of_day),
        }

    def restore(self, snap: dict | None) -> None:
        if not snap:
            return
        try:
            self.total_minutes = int(snap.get("total_minutes", 0) or 0)
            if "start_minute_of_day" in snap:
                self.start_minute_of_day = int(snap["start_minute_of_day"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("clock restore failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _state(self, *, delta: int, reason: str | None,
               scene_id: str | None) -> dict:
        return {
            "delta_minutes": int(delta),
            "total_minutes": int(self.total_minutes),
            "day": int(self.day),
            "hour_of_day": int(self.hour_of_day),
            "minute_of_day": int(self.minute_of_day),
            "hh_mm": self.hh_mm,
            "phase": self.phase,
            "reason": reason,
            "scene_id": scene_id,
        }

    def _publish(self, name: str, payload: dict) -> None:
        try:
            self.event_bus.publish(name, payload)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            logger.warning("clock publish %s failed: %s", name, exc)


__all__ = [
    "Clock",
    "Phase",
    "SHORT_REST_MIN",
    "LONG_REST_MIN",
]

