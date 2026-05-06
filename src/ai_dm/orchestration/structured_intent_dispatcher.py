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

_INTENT_VERBS = {"attack", "skill_check", "move", "travel", "interact", "speak", "use_item", "cast_spell"}
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
        turn_manager=None,
    ) -> None:
        self.event_bus = event_bus
        self.intent_router = intent_router
        self.combat = combat
        self.client = client
        self.turn_manager = turn_manager
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
        target_id = payload.get("target_id")
        logger.info(
            "npc_turn: player_intent verb=%s actor=%s target=%s payload_keys=%s",
            verb, actor_id, target_id, sorted(payload.keys()),
        )
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
        if payload.get("two_handed") is not None:
            ctx["two_handed"] = bool(payload["two_handed"])

        try:
            envelope = self.intent_router.handle(intent, ctx=ctx)
        except Exception as exc:  # noqa: BLE001
            logger.exception("intent_router failed: %s", exc)
            return

        narration = self._summarise(envelope)
        if narration:
            self._push_narration(payload.get("actor_id"), payload.get("user_id"), narration)
        # IMPORTANT: resolving an attack/skill/etc. via the intent
        # router does NOT advance combat. The spotlight stays on the
        # player until the End Turn macro fires. If you wonder why
        # Grokk "doesn't attack back" after you hit him, this is why.
        logger.info(
            "npc_turn: player intent %s resolved → narration=%r; "
            "turn NOT auto-ended (press End Turn to give NPCs a turn)",
            verb, narration,
        )

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
                logger.info(
                    "npc_turn: end_turn submitted by actor=%s; "
                    "turn_manager=%s",
                    payload.get("actor_id"),
                    type(self.turn_manager).__name__ if self.turn_manager else None,
                )
                self.combat.submit_action(kind="end_turn", payload=payload)
                if self.turn_manager is not None:
                    # Drives end_turn → (begin_round on wrap) →
                    # request_action so the spotlight actually
                    # advances and the NPC driver gets to act.
                    self.turn_manager.next_turn()
                    s = self.combat.state
                    next_actor = (
                        s.participants[s.current_index]
                        if s is not None and 0 <= s.current_index < len(s.participants)
                        else None
                    )
                else:
                    next_actor = self.combat.end_turn()
                if next_actor is not None:
                    logger.info(
                        "npc_turn: spotlight now on actor=%s name=%s "
                        "controller=%s team=%s hp=%s/%s",
                        getattr(next_actor, "actor_id", None),
                        getattr(next_actor, "name", None),
                        getattr(next_actor, "controller", None),
                        getattr(next_actor, "team", None),
                        getattr(next_actor, "hp", None),
                        getattr(next_actor, "max_hp", None),
                    )
                else:
                    logger.info("npc_turn: end_turn → round complete")
                msg = (
                    f"[end turn → next: {next_actor.name}]"
                    if next_actor is not None
                    else "[end turn → round complete]"
                )
            else:
                entry = self.combat.submit_action(kind=verb, payload=payload)
                msg = f"[{entry.actor_id} takes the {verb} action]"
        except Exception as exc:  # noqa: BLE001
            logger.exception("combat verb %s failed: %s", verb, exc)
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
        kind = d.get("type")
        details = d.get("details") or {}
        if kind == "attack":
            atk = details.get("attack") or {}
            dmg = details.get("damage") or {}
            outcome = "hit" if d.get("success") else "miss"
            tgt = d.get("target_id") or "target"
            roll_total = atk.get("total", "?")
            mod = atk.get("attack_modifier")
            ac = atk.get("target_ac")
            mod_str = f" ({mod:+d} vs AC {ac})" if isinstance(mod, int) and ac is not None else ""
            dmg_total = dmg.get("total") if isinstance(dmg, dict) else None
            tail = f" for {dmg_total}" if d.get("success") and dmg_total else ""
            crit = " CRIT" if atk.get("crit") else ""
            return f"[Attack vs {tgt}: {roll_total}{mod_str} → {outcome}{tail}{crit}]"
        if kind == "skill_check":
            outcome = "success" if d.get("success") else "failure"
            check = details.get("check") or details
            return (
                f"[{check.get('skill') or d.get('skill') or 'skill'} check: "
                f"{check.get('total', '?')} vs DC {check.get('dc', '?')} → {outcome}]"
            )
        return f"[{kind or 'action'} resolved]"

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

