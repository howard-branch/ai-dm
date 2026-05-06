"""Combat XP collector.

Listens on the in-process EventBus for combat lifecycle events and
banks defeated-foe XP into the runtime :class:`PartyState`:

  * ``combat.encounter_started`` → register every party participant in
    the PartyState (idempotent — re-running an encounter doesn't reset
    a member's xp/level).
  * ``rules.damage_applied`` → if the hit dropped a *foe* combatant to
    0 HP, stage that monster's XP into ``PartyState.pending_xp`` via
    :meth:`PartyState.record_kill`. Guarded by ``(encounter_id, actor_id)``
    so a flurry of overkill damage doesn't double-bank the same kill.
  * ``combat.encounter_ended`` → :meth:`PartyState.finalize_encounter`
    distributes the pool and publishes a player-visible chat line
    ("The party gains N XP each.") plus a level-up nudge when any
    member crosses an XP threshold.

Wired in :class:`ai_dm.app.container.Container.build` after the
:class:`CombatMachine` and :class:`PartyState` exist.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from ai_dm.game.party_state import PartyState
from ai_dm.orchestration.event_bus import EventBus

logger = logging.getLogger("ai_dm.orchestration.xp_collector")


class XPCollector:
    """Subscribe to combat events and award XP through PartyState."""

    def __init__(
        self,
        *,
        event_bus: EventBus,
        combat,                                # ai_dm.game.combat_machine.CombatMachine
        party_state: PartyState,
        client: Any = None,                    # FoundryClient — optional
    ) -> None:
        self.event_bus = event_bus
        self.combat = combat
        self.party_state = party_state
        self.client = client
        self._unsubs: list[Callable[[], None]] = []
        self._counted: set[tuple[str, str]] = set()  # (encounter_id, actor_id)

    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._unsubs:
            return
        self._unsubs.extend([
            self.event_bus.subscribe("combat.encounter_started", self._on_started),
            self.event_bus.subscribe("rules.damage_applied", self._on_damage),
            self.event_bus.subscribe("combat.encounter_ended", self._on_ended),
        ])
        logger.info("xp collector started")

    def stop(self) -> None:
        for u in self._unsubs:
            try: u()
            except Exception:  # noqa: BLE001
                pass
        self._unsubs.clear()

    # ------------------------------------------------------------------ #

    def _on_started(self, _payload: dict[str, Any]) -> None:
        s = getattr(self.combat, "state", None)
        if s is None:
            return
        for p in s.participants:
            if getattr(p, "team", None) == "party":
                # ``add_member`` is idempotent: once a member's xp/level
                # is registered we keep the cumulative value across
                # encounters.
                self.party_state.add_member(p.actor_id, level=1, xp=0)

    def _on_damage(self, payload: dict[str, Any]) -> None:
        s = getattr(self.combat, "state", None)
        if s is None or not s.encounter_id:
            return  # damage outside an encounter (trap, narrative) — skip
        outcome = payload.get("outcome") or {}
        if not outcome.get("dropped_to_zero"):
            return
        target_id = payload.get("target_id")
        if not target_id:
            return
        target = next((p for p in s.participants if p.actor_id == target_id), None)
        if target is None or getattr(target, "team", None) != "foe":
            return
        key = (s.encounter_id, target_id)
        if key in self._counted:
            return
        self._counted.add(key)
        xp = int(getattr(target, "xp", None) or 0)
        if xp <= 0:
            logger.debug("kill %s has 0 xp — skipping bank", target_id)
            return
        self.party_state.record_kill(
            monster_id=getattr(target, "stat_block_key", None),
            cr=getattr(target, "challenge_rating", None),
            xp=xp,
            encounter_id=s.encounter_id,
        )
        logger.info(
            "banked %d xp from %s (encounter=%s)",
            xp, target_id, s.encounter_id,
        )

    def _on_ended(self, payload: dict[str, Any]) -> None:
        encounter_id = payload.get("encounter_id")
        if self.party_state.pending_xp <= 0 or not self.party_state.members:
            return
        # Snapshot levels *before* finalising so we can announce the
        # actors whose level just increased. ``PartyState.finalize_encounter``
        # auto-bumps ``levels`` if their cumulative xp crosses a
        # threshold, so a post-finalise ``level_up_pending`` check
        # would only fire when they've also passed the *next* level.
        levels_before = dict(self.party_state.levels)
        per = self.party_state.finalize_encounter(encounter_id)
        even = next(iter(per.values()), 0) if per else 0
        if even <= 0:
            return
        # Drop counted-kills set scoped to the ended encounter so a
        # later encounter with the same id (replay / retry) banks again.
        self._counted = {
            (eid, aid) for (eid, aid) in self._counted if eid != encounter_id
        }
        lines = [f"The party gains {even} XP each."]
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
        # Two channels — keep the in-process planner / triggers in the
        # loop AND surface it to Foundry chat. Either may fail
        # independently (e.g. headless tests have no client).
        try:
            self.event_bus.publish("narrator.output_ready", {
                "narration": text,
                "dialogue": [],
                "metadata": {"kind": "xp_award"},
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


__all__ = ["XPCollector"]

