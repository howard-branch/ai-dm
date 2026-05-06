"""Project Python-authoritative combat mutations onto Foundry.

The :class:`ai_dm.rules.engine.RulesEngine` mutates :class:`CombatantState`
in place and publishes ``rules.damage_applied`` (and friends) on the bus.
This subscriber translates each event into the corresponding Foundry
``apply_damage`` command so the player's sheet/token bar updates.

Without this, attacks reduce HP only on the Python side; the Foundry
canvas never reflects the hit and the player thinks the attack did
nothing.
"""
from __future__ import annotations

import logging

from ai_dm.ai.schemas import Command as AICommand

logger = logging.getLogger("ai_dm.orchestration.combat_projector")


class CombatProjector:
    EVENT_NAME = "rules.damage_applied"

    def __init__(self, *, event_bus, command_router, combat=None) -> None:
        self.event_bus = event_bus
        self.command_router = command_router
        self.combat = combat
        self._unsubscribe = None

    def start(self) -> None:
        if self._unsubscribe is not None:
            return
        self._unsubscribe = self.event_bus.subscribe(
            self.EVENT_NAME, self._on_damage_applied
        )
        logger.info("combat_projector subscribed to %s", self.EVENT_NAME)

    def stop(self) -> None:
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            finally:
                self._unsubscribe = None

    # ------------------------------------------------------------------ #

    def _on_damage_applied(self, payload: dict) -> None:
        if self.command_router is None:
            logger.debug(
                "combat_projector: no command_router wired; skipping projection "
                "(payload=%r)", payload,
            )
            return
        target_id = payload.get("target_id")
        amount = int(payload.get("amount") or 0)
        if not target_id or amount <= 0:
            logger.debug(
                "combat_projector: skipping projection (target_id=%r amount=%r)",
                target_id, amount,
            )
            return
        damage_type = str(payload.get("damage_type") or "untyped")
        outcome = payload.get("outcome") or {}
        crit = bool(outcome.get("crit") or payload.get("crit") or False)

        # The CombatantState id (e.g. "mon.grukk") rarely matches the
        # Foundry actor's id or display name. The Foundry-side handler
        # falls back to a case-insensitive name lookup, so prefer the
        # participant's `name` whenever combat state knows it.
        #
        # ``target_id`` may itself be a fuzzy stand-in (e.g. ``"grukk"``
        # produced by the LLM director instead of ``"mon.grukk"``).
        # Try the wired fuzzy lookup first so we resolve to the real
        # CombatantState; fall back to the linear scan otherwise.
        actor_key = target_id
        s = getattr(self.combat, "state", None) if self.combat is not None else None
        resolved = None
        fuzzy = getattr(self.combat, "_actor_lookup", None) if self.combat is not None else None
        if callable(fuzzy):
            try:
                resolved = fuzzy(target_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("combat_projector: fuzzy lookup raised: %s", exc)
        if resolved is None and s is not None:
            for p in getattr(s, "participants", []) or []:
                if getattr(p, "actor_id", None) == target_id:
                    resolved = p
                    break
        if resolved is not None:
            pname = getattr(resolved, "name", None)
            if pname and pname != target_id:
                logger.info(
                    "combat_projector: resolved combatant %r → name=%r "
                    "(actor_id=%r) for Foundry actor lookup",
                    target_id, pname, getattr(resolved, "actor_id", None),
                )
                actor_key = pname
        else:
            logger.warning(
                "combat_projector: could not resolve target_id=%r to a live "
                "participant — forwarding raw id to Foundry (likely "
                "unknown_actor). Live participants=%s",
                target_id,
                [
                    {"actor_id": getattr(p, "actor_id", None),
                     "name": getattr(p, "name", None)}
                    for p in (getattr(s, "participants", []) or [])
                ] if s is not None else "(no encounter)",
            )

        # Sanity-check the outcome — if hp_before == hp_after == 0 the
        # damage almost certainly landed on a stub ActorRuleState
        # rather than the live combatant. Surface that as a warning so
        # the player ↔ canvas desync is obvious in the console.
        hp_before = outcome.get("hp_before")
        hp_after = outcome.get("hp_after")
        if hp_before == 0 and hp_after == 0 and amount > 0:
            logger.warning(
                "combat_projector: STUB DAMAGE detected for target_id=%r "
                "(hp_before=hp_after=0 despite amount=%d). The rules engine "
                "almost certainly ran against a fresh ActorRuleState — "
                "check the prior 'actor_lookup MISS' line for which id "
                "the LLM/director used.",
                target_id, amount,
            )

        logger.info(
            "combat_projector: rules.damage_applied → projecting apply_damage "
            "(target_id=%r actor_key=%r amount=%d type=%s crit=%s outcome=%r)",
            target_id, actor_key, amount, damage_type, crit, outcome,
        )
        cmd = AICommand(
            type="apply_damage",
            actor_id=actor_key,
            amount=amount,
            damage_type=damage_type,
            crit=crit,
        )
        try:
            result = self.command_router.dispatch([cmd])
            ok = getattr(result, "ok", None)
            errs = [
                f"{r.command.type}:{r.error}"
                for r in getattr(result, "results", []) or []
                if not getattr(r, "ok", True)
            ]
            if ok:
                logger.info(
                    "combat_projector: apply_damage dispatched OK for target_id=%r",
                    target_id,
                )
            else:
                logger.warning(
                    "combat_projector: apply_damage dispatch FAILED for "
                    "target_id=%r — errors=%s",
                    target_id, errs or "(none reported)",
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("apply_damage dispatch failed for %s: %s", target_id, exc)

