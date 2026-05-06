"""Authored interaction consequence applier.

Subscribes to ``roll.resolved``. When the underlying roll request
carried an authored ``correlation`` block (forwarded by
:meth:`ai_dm.ai.intent_router.IntentRouter._maybe_request_authored_roll`)
and the roll succeeded, this dispatcher executes the side-effects the
authored interaction promised:

* ``grants``       — list of item-pack keys; one ``give_item`` command
  is dispatched per entry, targeted at the rolling actor.
* ``starts_encounter`` — id forwarded to :class:`EncounterManager`.
* ``ends_encounter``   — id forwarded to :class:`EncounterManager`.
* ``ends_scene``       — dispatched as ``activate_scene`` and a
  ``scene.entered`` event published locally so triggers / planner state
  stay in sync.

XP is intentionally **not** applied here — that lives in
:class:`ai_dm.orchestration.xp_awarder.XPAwarder` so the two responsibilities
can be enabled / tested in isolation.

Each branch is wrapped in ``try``/``except``: a failed ``give_item``
must not block ``starts_encounter``, and so on.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from ai_dm.ai.schemas import Command as AICommand
from ai_dm.orchestration.event_bus import EventBus

logger = logging.getLogger("ai_dm.orchestration.interaction_effects")


class InteractionEffectsApplier:
    RESOLVED_EVENT = "roll.resolved"

    def __init__(
        self,
        *,
        event_bus: EventBus,
        command_router=None,                     # ai_dm.orchestration.command_router.CommandRouter
        encounter_manager=None,                  # ai_dm.game.encounter_manager.EncounterManager
    ) -> None:
        self.event_bus = event_bus
        self.command_router = command_router
        self.encounter_manager = encounter_manager
        self._unsubs: list[Callable[[], None]] = []
        self._applied_request_ids: set[str] = set()

    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._unsubs:
            return
        self._unsubs.append(self.event_bus.subscribe(
            self.RESOLVED_EVENT, self._on_resolved,
        ))
        logger.info("interaction effects applier started")

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
        if not corr:
            return
        if record.get("success") is not True:
            return
        rid = str(record.get("request_id") or "")
        if rid and rid in self._applied_request_ids:
            return
        if rid:
            self._applied_request_ids.add(rid)

        actor_id = record.get("actor_id") or corr.get("actor_id")
        scene_id = record.get("scene_id") or corr.get("scene_id")

        # ---- grants → give_item ------------------------------------ #
        grants = corr.get("grants")
        if grants:
            try:
                self._apply_grants(grants, actor_id=actor_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("apply grants(%s) failed: %s", grants, exc)

        # ---- starts_encounter -------------------------------------- #
        start_eid = corr.get("starts_encounter")
        if start_eid and self.encounter_manager is not None:
            try:
                self.encounter_manager.start_encounter(
                    str(start_eid), reason="authored_interaction",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "starts_encounter(%s) failed: %s", start_eid, exc,
                )

        # ---- ends_encounter ---------------------------------------- #
        end_eid = corr.get("ends_encounter")
        if end_eid and self.encounter_manager is not None:
            try:
                self.encounter_manager.end_encounter(
                    str(end_eid), reason="authored_interaction",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ends_encounter(%s) failed: %s", end_eid, exc,
                )

        # ---- ends_scene → activate_scene + scene.entered ----------- #
        next_scene = corr.get("ends_scene")
        if next_scene:
            try:
                self._transition_scene(str(next_scene), from_scene=scene_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ends_scene(%s) failed: %s", next_scene, exc,
                )

    # ------------------------------------------------------------------ #

    def _apply_grants(self, grants: Any, *, actor_id: str | None) -> None:
        if self.command_router is None:
            logger.info("grants ignored — no command_router wired")
            return
        if not actor_id:
            logger.info("grants ignored — no actor_id on roll record")
            return
        keys = self._coerce_grant_keys(grants)
        if not keys:
            return
        cmds = [
            AICommand(type="give_item", actor_id=actor_id, item_key=k, qty=1)
            for k in keys
        ]
        try:
            self.command_router.dispatch(cmds, atomic=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("give_item dispatch failed for %s: %s", keys, exc)

    @staticmethod
    def _coerce_grant_keys(grants: Any) -> list[str]:
        if isinstance(grants, str):
            return [grants]
        if isinstance(grants, dict):
            # {"loot.foo": 2, ...} or {"item": "loot.foo"} — best-effort.
            return [str(k) for k in grants.keys() if k]
        if isinstance(grants, Iterable):
            out: list[str] = []
            for g in grants:
                if isinstance(g, str) and g:
                    out.append(g)
                elif isinstance(g, dict):
                    key = g.get("item_key") or g.get("key") or g.get("id")
                    if key:
                        out.append(str(key))
            return out
        return []

    def _transition_scene(self, scene_id: str, *, from_scene: str | None) -> None:
        if self.command_router is not None:
            try:
                self.command_router.dispatch([
                    AICommand(type="activate_scene", scene_id=scene_id),
                ])
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "activate_scene(%s) dispatch failed: %s", scene_id, exc,
                )
        # Always publish so the planner / triggers / opening narrator
        # update even when no command_router is wired (e.g. tests).
        try:
            self.event_bus.publish("scene.entered", {
                "scene_id": scene_id,
                "from": from_scene,
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("scene.entered publish failed: %s", exc)


__all__ = ["InteractionEffectsApplier"]

