"""Story / interaction XP awarder.

Subscribes to ``roll.resolved`` and, when the underlying
``roll.requested`` carried an authored ``xp:`` field on its
``correlation`` block (forwarded by
:meth:`ai_dm.ai.intent_router.IntentRouter._maybe_request_authored_roll`
and re-published verbatim by
:meth:`ai_dm.orchestration.roll_request_dispatcher.RollRequestDispatcher._finalise`),
awards that XP through :meth:`PartyState.award_story_xp`.

Disjoint from :class:`ai_dm.orchestration.xp_collector.XPCollector` —
that one only listens to ``combat.*`` + ``rules.damage_applied``, so
the two can never double-count the same award.

Awarded only on **success** (``record["success"] is True``). Dedup is
keyed by ``record["request_id"]`` to survive re-deliveries from the
Foundry relay.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from ai_dm.game.party_state import PartyState
from ai_dm.orchestration.event_bus import EventBus

logger = logging.getLogger("ai_dm.orchestration.xp_awarder")


class XPAwarder:
    RESOLVED_EVENT = "roll.resolved"

    def __init__(
        self,
        *,
        event_bus: EventBus,
        party_state: PartyState,
        client: Any = None,
    ) -> None:
        self.event_bus = event_bus
        self.party_state = party_state
        self.client = client
        self._unsubs: list[Callable[[], None]] = []
        self._awarded_request_ids: set[str] = set()

    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._unsubs:
            return
        self._unsubs.append(self.event_bus.subscribe(
            self.RESOLVED_EVENT, self._on_resolved,
        ))
        logger.info("xp awarder started")

    def stop(self) -> None:
        for u in self._unsubs:
            try: u()
            except Exception:  # noqa: BLE001
                pass
        self._unsubs.clear()

    # ------------------------------------------------------------------ #

    def _on_resolved(self, payload: dict[str, Any]) -> None:
        record = payload.get("record") or {}
        corr = payload.get("correlation") or {}
        try:
            xp = int(corr.get("xp") or 0)
        except (TypeError, ValueError):
            return
        if xp <= 0:
            return
        # Only success grants story XP. ``None`` (no DC, free-form
        # roll) is treated as not-success — authored XP only triggers
        # on a real check outcome.
        if record.get("success") is not True:
            return
        rid = str(record.get("request_id") or "")
        if rid and rid in self._awarded_request_ids:
            return
        if rid:
            self._awarded_request_ids.add(rid)
        # Lazy-register the rolling actor so story XP can fire before
        # the first encounter (which is what populates members via
        # XPCollector._on_started).
        actor_id = record.get("actor_id") or corr.get("actor_id")
        if actor_id and actor_id not in self.party_state.members:
            self.party_state.add_member(actor_id, level=1, xp=0)
        if not self.party_state.members:
            logger.info("xp award skipped — no party members registered")
            return
        levels_before = dict(self.party_state.levels)
        per = self.party_state.award_story_xp(
            xp,
            source=corr.get("feature") or corr.get("source"),
            scene_id=record.get("scene_id"),
        )
        even = next(iter(per.values()), 0) if per else 0
        if even <= 0:
            return
        lines = [f"The party gains {even} story XP each."]
        for aid in self.party_state.members:
            old = levels_before.get(aid, 1)
            new = self.party_state.levels.get(aid, 1)
            if new > old:
                lines.append(f"{aid} reached level {new}!")
            elif self.party_state.level_up_pending(aid):
                lines.append(f"{aid} can level up!")
        self._publish_narration("\n".join(lines))

    # ------------------------------------------------------------------ #

    def _publish_narration(self, text: str) -> None:
        try:
            self.event_bus.publish("narrator.output_ready", {
                "narration": text,
                "dialogue": [],
                "metadata": {"kind": "story_xp_award"},
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("xp narrator.output_ready publish failed: %s", exc)
        if self.client is not None:
            try:
                self.client.send_event("narration", {
                    "actor_id": None,
                    "user_id": None,
                    "narration": text,
                    "dialogue": [],
                    "commands_ok": True,
                    "whisper_to": None,
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning("xp send_event(narration) failed: %s", exc)


__all__ = ["XPAwarder"]

