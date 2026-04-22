"""Bridge ``foundry.player_intent`` events into the rules engine.

Phase 3 macros (Attack / Dash / Dodge / End Turn / Use Skill) bypass
the LLM entirely and POST a structured intent. We map each verb to:

* ``attack`` / ``skill_check`` / ``move`` / ``interact`` / ``speak`` /
  ``use_item`` →  build a :class:`PlayerIntent` and route through
  :class:`IntentRouter` (which delegates to :class:`ActionResolver`
  for mechanical ones and to :class:`CommandRouter` for movement).
* ``dash`` / ``dodge`` / ``end_turn`` →  forwarded straight to
  :class:`CombatMachine` because they are not modelled in
  :data:`IntentType`.

Any resolution is summarised back to Foundry as a ``narration`` event
so the chat log shows e.g. ``[Attack vs goblin: 17 → hit for 8]``.
"""
from __future__ import annotations

import logging
from typing import Any

from ai_dm.ai.intent_schemas import PlayerIntent

logger = logging.getLogger("ai_dm.orchestration.structured_intent_dispatcher")

_INTENT_VERBS = {"attack", "skill_check", "move", "interact", "speak", "use_item", "cast_spell"}
_COMBAT_VERBS = {"dash", "dodge", "end_turn"}


class StructuredIntentDispatcher:
    EVENT_NAME = "foundry.player_intent"

    def __init__(
        self,
        *,
        event_bus,
        intent_router=None,
        combat=None,
        client=None,
    ) -> None:
        self.event_bus = event_bus
        self.intent_router = intent_router
        self.combat = combat
        self.client = client
        self._unsubscribe = None

    def start(self) -> None:
        if self._unsubscribe is not None:
            return
        self._unsubscribe = self.event_bus.subscribe(
            self.EVENT_NAME, self._on_intent
        )
        logger.info("structured_intent dispatcher subscribed")

    def stop(self) -> None:
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            finally:
                self._unsubscribe = None

    # ------------------------------------------------------------------ #

    def _on_intent(self, payload: dict[str, Any]) -> None:
        verb = payload.get("type")
        actor_id = payload.get("actor_id")
        if not verb:
            logger.warning("player_intent missing type: %s", payload)
            return

        if verb in _COMBAT_VERBS:
            self._handle_combat(verb, payload)
            return

        if verb in _INTENT_VERBS and self.intent_router is not None:
            self._handle_intent(verb, payload)
            return

        logger.warning("unsupported player_intent verb=%s actor=%s", verb, actor_id)

    # ------------------------------------------------------------------ #

    def _handle_intent(self, verb: str, payload: dict[str, Any]) -> None:
        intent_kwargs: dict[str, Any] = {
            "type": verb,
            "actor_id": payload.get("actor_id"),
            "target_id": payload.get("target_id"),
            "target_anchor": payload.get("target_anchor"),
            "weapon": payload.get("weapon"),
            "skill": payload.get("skill"),
            "spell": payload.get("spell"),
            "dc": payload.get("dc"),
            "modifier": payload.get("modifier"),
            "raw_text": payload.get("raw_text") or f"[macro:{verb}]",
            "confidence": 1.0,
        }
        intent_kwargs = {k: v for k, v in intent_kwargs.items() if v is not None}
        try:
            intent = PlayerIntent(**intent_kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("invalid player_intent payload: %s (%s)", payload, exc)
            return

        ctx: dict[str, Any] = {}
        if payload.get("scene_id"):
            ctx["scene_id"] = payload["scene_id"]
        if payload.get("attack_modifier") is not None:
            ctx["attack_modifier"] = payload["attack_modifier"]
        if payload.get("damage_dice"):
            ctx["damage_dice"] = payload["damage_dice"]

        try:
            envelope = self.intent_router.handle(intent, ctx=ctx)
        except Exception as exc:  # noqa: BLE001
            logger.exception("intent_router failed: %s", exc)
            return

        narration = self._summarise(envelope)
        if narration:
            self._push_narration(payload.get("actor_id"), payload.get("user_id"), narration)

    def _handle_combat(self, verb: str, payload: dict[str, Any]) -> None:
        if self.combat is None or self.combat.state is None:
            self._push_narration(
                payload.get("actor_id"),
                payload.get("user_id"),
                f"[{verb}: no encounter in progress]",
            )
            return
        try:
            if verb == "end_turn":
                self.combat.submit_action(kind="end_turn", payload=payload)
                next_actor = self.combat.end_turn()
                msg = (
                    f"[end turn → next: {next_actor.name}]"
                    if next_actor is not None
                    else "[end turn → round complete]"
                )
            else:
                entry = self.combat.submit_action(kind=verb, payload=payload)
                msg = f"[{entry.actor_id} takes the {verb} action]"
        except Exception as exc:  # noqa: BLE001
            logger.warning("combat verb %s failed: %s", verb, exc)
            msg = f"[{verb} failed: {exc}]"

        self._push_narration(payload.get("actor_id"), payload.get("user_id"), msg)

    # ------------------------------------------------------------------ #

    def _summarise(self, envelope) -> str:
        if envelope.rejected_reason:
            return f"[rejected: {envelope.rejected_reason}]"
        res = envelope.resolution
        if res is None:
            return ""
        d = res.to_dict()
        if d.get("type") == "attack":
            outcome = "hit" if d.get("hit") else "miss"
            dmg = d.get("damage")
            tgt = d.get("target_id") or "target"
            return f"[Attack vs {tgt}: {d.get('attack_total', '?')} → {outcome}" + (
                f" for {dmg}" if dmg else ""
            ) + "]"
        if d.get("type") == "skill_check":
            outcome = "success" if d.get("success") else "failure"
            return (
                f"[{d.get('skill', 'skill')} check: {d.get('total', '?')}"
                f" vs DC {d.get('dc', '?')} → {outcome}]"
            )
        return f"[{d.get('type', 'action')} resolved]"

    def _push_narration(self, actor_id, user_id, narration: str) -> None:
        if self.client is None or not narration:
            return
        try:
            self.client.send_event(
                "narration",
                {
                    "actor_id": actor_id,
                    "user_id": user_id,
                    "narration": narration,
                    "dialogue": [],
                    "commands_ok": True,
                    "whisper_to": None,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("send_event(narration) failed: %s", exc)

