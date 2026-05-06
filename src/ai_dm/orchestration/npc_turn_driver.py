"""Auto-act on the active turn when the spotlight lands on an AI combatant.

Subscribes to ``combat.actor_highlighted``; when the highlighted actor is
controlled by AI (``controller=='ai'``), it picks a hostile target,
resolves an attack through the rules engine, ends the turn via
:class:`TurnManager`, and (if the next actor is also AI) loops.

This is intentionally a *minimal* deterministic driver:
* picks the first surviving opposing-team participant as the target,
* uses the combatant's first :class:`MonsterAction`/`MonsterAttack` if
  one is present, otherwise a 1d4+0 untyped slap so the loop never
  stalls,
* never moves; the encounter is assumed to start with foes already in
  range.

It exists so monsters *do something* on their turn instead of leaving
the player staring at a highlighted goblin forever. A smarter LLM- or
behaviour-tree-driven planner can swap in later by replacing
``_choose_action`` / ``_choose_target``.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("ai_dm.orchestration.npc_turn_driver")


class NPCTurnDriver:
    EVENT_NAME = "combat.actor_highlighted"

    def __init__(
        self,
        *,
        event_bus,
        combat=None,                # ai_dm.game.combat_machine.CombatMachine
        rules=None,                 # ai_dm.rules.engine.RulesEngine
        turn_manager=None,          # ai_dm.orchestration.turn_manager.TurnManager
        client=None,                # ai_dm.foundry.client.FoundryClient (for chat echo)
        max_chain: int = 8,
    ) -> None:
        self.event_bus = event_bus
        self.combat = combat
        self.rules = rules
        self.turn_manager = turn_manager
        self.client = client
        self.max_chain = max_chain
        self._unsubscribe = None
        self._reentry_guard = False
        # Synchronous event bus: ``request_action`` for the next actor
        # fires inside our own ``_advance`` call, re-entering
        # ``_on_highlighted`` while we're still finishing the current
        # NPC's turn. We can't just bail (the next actor might be
        # another AI combatant — e.g. goblin#2 right after goblin#1 —
        # and we'd silently freeze the round). Instead we stash the
        # reentrant highlight and drain it in a loop once the outer
        # turn unwinds.
        self._pending_actor_id: str | None = None

    def start(self) -> None:
        if self._unsubscribe is not None:
            return
        self._unsubscribe = self.event_bus.subscribe(
            self.EVENT_NAME, self._on_highlighted
        )
        logger.info("npc_turn_driver subscribed to %s", self.EVENT_NAME)

    def stop(self) -> None:
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            finally:
                self._unsubscribe = None

    # ------------------------------------------------------------------ #

    def _on_highlighted(self, payload: dict) -> None:
        actor_id = payload.get("actor_id")
        logger.info("npc_turn: _on_highlighted received payload=%s", payload)
        if self._reentry_guard:
            # Re-entered synchronously from inside our own _advance →
            # turn_manager.next_turn() → combat.request_action() chain.
            # Stash the new actor; the outer loop will pick it up.
            self._pending_actor_id = actor_id
            logger.info(
                "npc_turn: queued reentrant highlight actor=%s "
                "(driving previous turn)", actor_id,
            )
            return

        self._reentry_guard = True
        try:
            self._pending_actor_id = actor_id
            chain = 0
            while self._pending_actor_id is not None and chain < self.max_chain:
                next_id = self._pending_actor_id
                self._pending_actor_id = None
                chain += 1
                self._drive(next_id)
            if self._pending_actor_id is not None:
                logger.warning(
                    "npc_turn: max_chain=%d hit — dropping pending actor=%s",
                    self.max_chain, self._pending_actor_id,
                )
                self._pending_actor_id = None
        finally:
            self._reentry_guard = False

    def _drive(self, actor_id: str | None) -> None:
        if self.combat is None or self.combat.state is None:
            logger.info(
                "npc_turn: skipped — combat=%s state=%s",
                self.combat,
                getattr(self.combat, "state", None) if self.combat else None,
            )
            return
        actor = self._find_participant(actor_id)
        if actor is None:
            known = [p.actor_id for p in self.combat.state.participants]
            logger.info(
                "npc_turn: skipped — actor_id=%r not in participants=%s",
                actor_id, known,
            )
            return
        controller = getattr(actor, "controller", None)
        team = getattr(actor, "team", None)
        hp = getattr(actor, "hp", None)
        max_hp = getattr(actor, "max_hp", None)
        logger.info(
            "npc_turn: highlighted actor=%s name=%s controller=%s team=%s hp=%s/%s",
            actor_id, getattr(actor, "name", None), controller, team, hp, max_hp,
        )
        if controller != "ai":
            logger.info(
                "npc_turn: skipped — actor.controller=%r is not 'ai' "
                "(player turn; waiting for end_turn macro)",
                controller,
            )
            return
        if int(hp or 0) <= 0:
            # Down/dead — just end its turn so the round advances.
            logger.info("npc_turn: %s is down (hp=%s) — passing turn", actor_id, hp)
            self._advance(actor)
            return

        self._take_turn(actor)

    # ------------------------------------------------------------------ #

    def _take_turn(self, actor: Any) -> None:
        actions = list(getattr(actor, "actions", None) or [])
        logger.info(
            "npc_turn: %s taking turn (hp=%s/%s ac=%s actions=%d)",
            actor.actor_id, getattr(actor, "hp", None),
            getattr(actor, "max_hp", None), getattr(actor, "ac", None),
            len(actions),
        )
        target = self._choose_target(actor)
        if target is None:
            candidates = [
                (p.actor_id, getattr(p, "team", None), getattr(p, "hp", None))
                for p in self.combat.state.participants
                if p.actor_id != actor.actor_id
            ]
            logger.info(
                "npc_turn: %s has no valid target — passing "
                "(my_team=%s, candidates=%s)",
                actor.actor_id, getattr(actor, "team", None), candidates,
            )
            self._advance(actor)
            return

        attack_mod, dice, dmg_type = self._choose_action(actor)
        logger.info(
            "npc_turn: %s → %s using mod=%+d dice=%s type=%s",
            actor.actor_id, target.actor_id, attack_mod, dice, dmg_type,
        )
        log_extra: dict[str, Any] = {
            "attacker": actor.actor_id,
            "target": target.actor_id,
        }

        if self.rules is None:
            logger.info("npc_turn: no rules engine; passing %s", actor.actor_id)
            self._advance(actor)
            return

        try:
            logger.info(
                "npc_turn: rolling attack %s → %s (target hp=%s/%s ac=%s)",
                actor.actor_id, target.actor_id,
                getattr(target, "hp", None), getattr(target, "max_hp", None),
                getattr(target, "ac", None),
            )
            atk = self.rules.attack(actor, target, attack_modifier=attack_mod)
            damage_total = 0
            crit = bool(getattr(atk, "crit", False))
            if atk.hit:
                dmg = self.rules.damage(
                    target, dice=dice, bonus=0,
                    damage_type=dmg_type, crit=crit,
                )
                damage_total = dmg.total
                self.rules.apply_damage(
                    target, dmg.total,
                    damage_type=dmg_type, crit=crit,
                )
                logger.info(
                    "npc_turn: %s HIT %s for %d %s (crit=%s) → target hp=%s/%s",
                    actor.actor_id, target.actor_id, damage_total, dmg_type, crit,
                    getattr(target, "hp", None), getattr(target, "max_hp", None),
                )
            else:
                logger.info(
                    "npc_turn: %s MISSED %s (attack_mod=%+d vs ac=%s)",
                    actor.actor_id, target.actor_id, attack_mod,
                    getattr(target, "ac", None),
                )
            log_extra.update({
                "hit": atk.hit, "crit": crit, "damage": damage_total,
                "target_hp": target.hp,
            })
            logger.info("npc_turn resolved %s", log_extra)
            self._push_chat(
                actor, target,
                hit=atk.hit, crit=crit, damage=damage_total,
            )
            try:
                self.combat.submit_action(
                    kind="attack",
                    payload={"actor_id": actor.actor_id,
                             "target_id": target.actor_id},
                    result={"hit": atk.hit, "damage": damage_total,
                            "crit": crit},
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("npc_turn: submit_action failed: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("npc_turn rules failed for %s: %s",
                             actor.actor_id, exc)

        self._advance(actor)

    # ------------------------------------------------------------------ #

    def _advance(self, actor: Any) -> None:
        if self.turn_manager is None:
            logger.info(
                "npc_turn: no turn_manager → cannot advance after %s; "
                "encounter will stall on this actor",
                getattr(actor, "actor_id", "?"),
            )
            return
        try:
            logger.info("npc_turn: advancing turn after %s", actor.actor_id)
            self.turn_manager.next_turn()
        except Exception as exc:  # noqa: BLE001
            logger.exception("npc_turn next_turn failed: %s", exc)

    def _find_participant(self, actor_id: str | None) -> Any | None:
        if not actor_id or self.combat.state is None:
            return None
        for p in self.combat.state.participants:
            if p.actor_id == actor_id:
                return p
        return None

    def _choose_target(self, actor: Any) -> Any | None:
        my_team = getattr(actor, "team", None)
        # Prefer party (if I'm a foe) → first surviving member.
        for p in self.combat.state.participants:
            if p.actor_id == actor.actor_id:
                continue
            if int(getattr(p, "hp", 0) or 0) <= 0:
                continue
            their_team = getattr(p, "team", None)
            # Hostile if teams differ and neither side is neutral-only.
            if their_team and my_team and their_team != my_team:
                if "neutral" in (their_team, my_team):
                    continue
                return p
        return None

    def _choose_action(self, actor: Any) -> tuple[int, str, str]:
        """Return (attack_modifier, damage_dice, damage_type).

        Pulls from the first :class:`MonsterAttack` / :class:`MonsterAction`
        if one is authored; otherwise falls back to a tiny default so the
        encounter still flows.

        The :class:`MonsterAttack` model carries ``damage`` as a string
        (``"1d12+3"``) plus a separate ``damage_type``; older code in
        this driver expected a dict-shaped damage with ``.dice`` /
        ``.type`` and silently fell back to ``1d4 bludgeoning`` for
        every NPC turn (so Grukk swung an unarmed strike instead of
        his greataxe). We now read both shapes.
        """
        actions = list(getattr(actor, "actions", None) or [])
        for a in actions:
            atk = getattr(a, "attack", None)
            if atk is None:
                continue
            mod = int(getattr(atk, "to_hit", 0) or 0)
            dmg = getattr(atk, "damage", None)
            dtype = (
                getattr(atk, "damage_type", None)
                or (dmg.get("type") if isinstance(dmg, dict) else None)
                or "bludgeoning"
            )
            if isinstance(dmg, str) and dmg.strip():
                dice = dmg.strip()
            elif isinstance(dmg, dict):
                dice = str(dmg.get("dice") or "1d4")
            else:
                dice = str(getattr(dmg, "dice", None) or "1d4")
            return (mod, dice, str(dtype))
        # Bare unarmed fallback — proficiency-ish.
        return (2, "1d4", "bludgeoning")

    def _push_chat(self, actor, target, *, hit, crit, damage) -> None:
        if self.client is None:
            return
        msg = (
            f"[{actor.name} → {target.name}: "
            + ("HIT" if hit else "miss")
            + (f" for {damage}" if hit else "")
            + (" (CRIT)" if crit else "")
            + "]"
        )
        try:
            self.client.send_event("narration", {
                "actor_id": actor.actor_id,
                "user_id": None,
                "narration": msg,
                "dialogue": [],
                "commands_ok": True,
                "whisper_to": None,
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("npc_turn chat echo failed: %s", exc)

