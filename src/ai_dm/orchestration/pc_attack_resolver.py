"""Resume a player chat-driven attack after the Foundry roll dialog.

When :class:`IntentRouter` defers a player ``attack X`` to the
:class:`RollRequestDispatcher`, the d20 is rolled by the player on
the Foundry side. When the resolved roll comes back over the bus as
``roll.resolved`` with ``correlation.kind == "pc_attack"``, this
subscriber reconstructs a synthetic :class:`PlayerIntent` and calls
the :class:`ActionResolver` with the rolled d20 baked into ``ctx``
as ``preroll_d20``. The resolver then runs the attack the same way
it always has — applying damage, consuming the action, breaking
stealth, projecting ``apply_damage`` to Foundry — only the d20 came
from the player instead of the server-side dice roller.

Threading
---------
``roll.resolved`` is published from the dispatcher's
``_on_resolved`` handler, which runs in the Foundry websocket
*receive* thread (the one that delivers ``foundry.player_roll_resolved``
envelopes). If we ran the resume chain synchronously here the
:class:`CombatProjector`'s ``apply_damage`` would block waiting for
a Foundry ack that can never arrive — the very thread that would
deliver it is the one we're holding. (This is the same re-entrant
deadlock that :class:`PlayerInputDispatcher` documents.)

So ``_on_resolved`` does the bare minimum (validate + queue) and
hands the resolve / apply-damage / advance-turn work to a daemon
worker, freeing the recv loop to pump responses immediately.

The follow-up narration turn synthesised by
:meth:`RollRequestDispatcher._enqueue_followup` runs in parallel and
gives the LLM a "[roll-result attack/... = N vs AC X → ...]" line
to riff on.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any

logger = logging.getLogger("ai_dm.orchestration.pc_attack_resolver")


class PCAttackResolver:
    EVENT_NAME = "roll.resolved"

    def __init__(
        self,
        *,
        event_bus,
        action_resolver,
        turn_manager=None,                  # ai_dm.orchestration.turn_manager.TurnManager
        combat=None,                        # ai_dm.game.combat_machine.CombatMachine
    ) -> None:
        self.event_bus = event_bus
        self.action_resolver = action_resolver
        self.turn_manager = turn_manager
        self.combat = combat
        self._unsubscribe = None
        self._work_queue: "queue.Queue[dict | None]" = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        # When True, ``_on_resolved`` runs synchronously in the caller
        # thread (no worker spawned). Set by tests via ``start(synchronous=True)``
        # so the existing assert-after-publish style continues to work.
        self._synchronous = False

    def start(self, *, synchronous: bool = False) -> None:
        if self._unsubscribe is not None:
            return
        self._synchronous = synchronous
        if not synchronous:
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="pc-attack-resolver",
                daemon=True,
            )
            self._worker.start()
        self._unsubscribe = self.event_bus.subscribe(
            self.EVENT_NAME, self._on_resolved,
        )
        logger.info(
            "pc_attack_resolver subscribed to %s (synchronous=%s)",
            self.EVENT_NAME, synchronous,
        )

    def stop(self) -> None:
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            finally:
                self._unsubscribe = None
        self._stop.set()
        try:
            self._work_queue.put_nowait(None)
        except Exception:  # noqa: BLE001
            pass
        if self._worker is not None:
            self._worker.join(timeout=1.0)
            self._worker = None

    # ------------------------------------------------------------------ #

    def _on_resolved(self, payload: dict[str, Any]) -> None:
        corr = payload.get("correlation") or {}
        if corr.get("kind") != "pc_attack":
            return
        # Validate up-front — cheap, recv-thread-safe — so we don't
        # spawn a worker for malformed payloads.
        if not (corr.get("actor_id") or (payload.get("record") or {}).get("actor_id")):
            logger.warning(
                "pc_attack roll.resolved missing actor_id: corr=%s record=%s",
                corr, payload.get("record"),
            )
            return
        if not corr.get("target_id"):
            logger.warning(
                "pc_attack roll.resolved missing target_id: corr=%s", corr,
            )
            return
        if self._synchronous or self._worker is None:
            # Test mode (or stop/start race): just run inline.
            self._handle(payload)
            return
        try:
            self._work_queue.put_nowait(payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pc_attack: enqueue failed (running inline as fallback): %s",
                exc,
            )
            self._handle(payload)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                payload = self._work_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if payload is None:
                return
            try:
                self._handle(payload)
            except Exception:  # noqa: BLE001
                logger.exception("pc_attack worker raised")

    # ------------------------------------------------------------------ #

    def _handle(self, payload: dict[str, Any]) -> None:
        corr = payload.get("correlation") or {}
        record = payload.get("record") or {}
        actor_id = corr.get("actor_id") or record.get("actor_id")
        target_id = corr.get("target_id")

        # Pull the natural d20 (before the +mod) out of the record so
        # the rules engine can re-apply mods consistently with whatever
        # the live attacker stat block dictates now (e.g. the inventory
        # has changed since the dialog opened).
        d20 = self._extract_d20(record)
        if d20 is None:
            logger.warning(
                "pc_attack roll.resolved had no d20 in record: %s — "
                "falling back to server-rolled attack",
                record,
            )

        intent = _SyntheticAttackIntent(
            actor_id=str(actor_id),
            target_id=str(target_id),
            weapon=corr.get("weapon"),
            raw_text=corr.get("raw_text") or "",
        )
        ctx: dict[str, Any] = {
            "scene_id": corr.get("scene_id"),
            "actor_id": actor_id,
            "preroll_d20": d20,
            # Mark the resumed turn so IntentRouter (if it ever sees
            # this intent again) doesn't re-defer.
            "origin": "pc_attack_resume",
        }
        for k, v in (corr.get("ctx_overrides") or {}).items():
            ctx.setdefault(k, v)

        try:
            resolution = self.action_resolver.resolve_intent(intent, ctx)
        except Exception:  # noqa: BLE001
            logger.exception("pc_attack resume resolve_intent failed")
            return
        logger.info(
            "pc_attack resumed: %s → %s | %s",
            actor_id, target_id, getattr(resolution, "summary", ""),
        )

        # End the player's combat turn so initiative advances to the
        # next actor (typically a foe whose ``NPCTurnDriver`` will
        # immediately swing back). Without this the encounter sits
        # waiting on the PC forever and the player's complaint
        # "the orc never fights back" is the inevitable result.
        self._advance_turn_if_combat_live(str(actor_id))

    def _advance_turn_if_combat_live(self, actor_id: str) -> None:
        if self.turn_manager is None or self.combat is None:
            return
        state = getattr(self.combat, "state", None)
        if state is None or getattr(state, "phase", None) in (None, "idle", "ended"):
            return
        # Only advance when it's actually the attacking PC's turn —
        # otherwise we'd skip an unrelated NPC's slot.
        try:
            cur = state.participants[state.current_index]
        except (AttributeError, IndexError):
            cur = None
        cur_id = getattr(cur, "actor_id", None)
        # ``actor_id`` is typically the opaque Foundry id (e.g.
        # ``"yZyrzj2luKwJSfPI"``) while ``cur_id`` is the
        # CombatantState id (``"hero"`` / ``"jon"``). Resolve through
        # the action_resolver's actor_lookup so the comparison sees
        # the same identity the rules engine uses; otherwise every
        # PC chat attack is misclassified as an opportunity attack
        # and the turn pointer never advances → "the orc never
        # fights back".
        attacker_resolved = self._resolve_attacker_id(actor_id)
        if cur_id and attacker_resolved and cur_id != attacker_resolved:
            logger.info(
                "pc_attack: not advancing turn — current=%s, attacker=%s "
                "(resolved=%s)",
                cur_id, actor_id, attacker_resolved,
            )
            return
        try:
            self.turn_manager.next_turn()
        except Exception:  # noqa: BLE001
            logger.exception("pc_attack: turn_manager.next_turn() failed")

    def _resolve_attacker_id(self, actor_id: str) -> str | None:
        """Translate ``actor_id`` (often a Foundry opaque id) to the
        live CombatantState's ``actor_id`` (``"jon"`` / ``"hero"``)
        via the same fuzzy lookup the resolver uses.
        """
        try:
            lookup = getattr(self.action_resolver, "actor_lookup", None)
            if not callable(lookup):
                return actor_id
            who = lookup(actor_id)
            if who is None:
                return actor_id
            resolved = getattr(who, "actor_id", None)
            return resolved or actor_id
        except Exception:  # noqa: BLE001
            return actor_id

    @staticmethod
    def _extract_d20(record: dict[str, Any]) -> int | None:
        """Best-effort retrieval of the natural d20 face from the
        :class:`RollRecord` dict. Foundry sends back ``rolls`` /
        ``kept`` lists in addition to ``total``; we prefer the kept
        die so advantage / disadvantage already collapsed correctly.
        """
        for key in ("kept", "rolls", "dice"):
            seq = record.get(key)
            if isinstance(seq, list) and seq:
                try:
                    n = int(seq[0])
                    if 1 <= n <= 20:
                        return n
                except (TypeError, ValueError):
                    continue
        # Last-ditch: total minus modifier (when modifier is reported).
        try:
            total = int(record.get("total") or 0)
            mod = int(record.get("modifier") or 0)
            n = total - mod
            if 1 <= n <= 20:
                return n
        except (TypeError, ValueError):
            return None
        return None


class _SyntheticAttackIntent:
    """Tiny duck-typed stand-in for :class:`PlayerIntent` so we can
    feed :meth:`ActionResolver.resolve_intent` without dragging in
    the pydantic schema (the resolver only reads attributes via
    ``getattr``).
    """

    type = "attack"

    def __init__(
        self,
        *,
        actor_id: str,
        target_id: str,
        weapon: str | None,
        raw_text: str,
    ) -> None:
        self.actor_id = actor_id
        self.target_id = target_id
        self.weapon = weapon
        self.raw_text = raw_text

