"""Player-facing roll request lifecycle.

Three responsibilities:

1. **Emit**: when the AI's narration carries ``dice_requests`` items
   (or any other code path wants to ask the player to roll), publish a
   ``request_player_roll`` event to Foundry. The browser-side module
   (``foundry/module/scripts/roll_prompt.js``) renders a chat card
   with a clickable "Roll" button + Dice So Nice animation.

2. **Resolve**: subscribe to ``foundry.player_roll_resolved`` events
   sent back when the player clicks the button. Match by ``request_id``,
   write the outcome to the :class:`RollLog`, publish a local
   ``roll.resolved`` event so triggers can react, and synthesise a
   follow-up ``foundry.player_input`` so the Director runs the next
   narration turn with the roll outcome in context.

3. **Timeout**: if no answer arrives within ``timeout_s`` (default 30s),
   either auto-roll via :class:`DMRoller` (default), cancel the prompt,
   or whisper the GM. Configurable per :class:`Settings`.

This dispatcher is thread-safe. The reaper runs in a daemon thread
that wakes every second to evict expired requests.

Threading
---------
``foundry.player_roll_resolved`` is republished from the Foundry client's
WebSocket *receive* loop. If we ran the full ``_finalise`` chain
synchronously in that thread, every downstream subscriber of
``roll.resolved`` (PCAttackResolver, InteractionEffectsApplier,
XPAwarder, …) would inherit the recv thread — and any one of them that
dispatches a Foundry command (e.g. ``apply_damage`` via
:class:`CombatProjector`) would block waiting on a Foundry response that
can never arrive, because the very thread that would deliver it is the
one we're holding. Hence the 30-second ``apply_damage`` timeouts and
the parallel "relay did not respond within 2.0s" warnings observed on
PC attacks.

To break that re-entrant deadlock — the same one
:class:`PlayerInputDispatcher` documents — ``_on_resolved`` does the
bare minimum (validate + enqueue) and hands the heavy ``_finalise``
fan-out to a daemon worker thread, freeing the recv loop to pump
response envelopes immediately.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from ai_dm.ai.schemas import DiceRequest
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.persistence.roll_log import RollLog, RollRecord
from ai_dm.rules.dm_rolls import DMRoller

logger = logging.getLogger("ai_dm.orchestration.rolls")

# Mapping skill → ability for default modifier lookup. Lazy-imported
# from the rules reference to avoid a hard dependency in tests.
try:
    from ai_dm.rules.dnd5e_reference import SKILL_TO_ABILITY  # type: ignore
except Exception:  # noqa: BLE001
    SKILL_TO_ABILITY = {}


_ON_TIMEOUT_VALUES = ("auto_roll", "cancel", "gm_prompt")


@dataclass
class _Pending:
    request_id: str
    actor_id: str | None
    scene_id: str | None
    roll_type: str
    key: str | None
    dc: int | None
    ac: int | None
    formula: str | None
    advantage: str
    visibility: str
    prompt_text: str
    reason: str | None
    issued_at: float
    expires_at: float
    correlation: dict[str, Any] = field(default_factory=dict)


class RollRequestDispatcher:
    """Lifecycle: emit → await → resolve → resume."""

    REQUEST_EVENT = "roll.requested"          # internal: anyone can publish
    RESOLVED_EVENT = "roll.resolved"          # internal: triggers can subscribe
    INBOUND_EVENT = "foundry.player_roll_resolved"

    def __init__(
        self,
        *,
        event_bus: EventBus,
        client: Any = None,                    # FoundryClient
        roll_log: RollLog | None = None,
        dm_roller: DMRoller | None = None,
        timeout_s: float = 30.0,
        on_timeout: str = "auto_roll",
        enabled: bool = True,
    ) -> None:
        self.event_bus = event_bus
        self.client = client
        self.roll_log = roll_log
        self.dm_roller = dm_roller
        self.timeout_s = float(timeout_s)
        if on_timeout not in _ON_TIMEOUT_VALUES:
            logger.warning("unknown on_timeout=%r, falling back to auto_roll", on_timeout)
            on_timeout = "auto_roll"
        self.on_timeout = on_timeout
        self.enabled = enabled

        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()
        self._unsubs: list[Callable[[], None]] = []
        self._reaper: threading.Thread | None = None
        self._stop = threading.Event()
        # Worker queue + thread for off-loading ``_finalise`` (which
        # publishes ``roll.resolved`` and the synthetic
        # ``foundry.player_input``) off the websocket recv thread.
        # See module docstring for why this matters.
        self._work_queue: "queue.Queue[tuple[_Pending, RollRecord, str] | None]" = (
            queue.Queue()
        )
        self._worker: threading.Thread | None = None
        # When True, ``_on_resolved`` runs synchronously in the caller
        # thread (no worker spawned). Set by tests via
        # ``start(synchronous=True)`` so the existing assert-after-publish
        # style continues to work.
        self._synchronous = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self, *, synchronous: bool = False) -> None:
        if self._unsubs:
            return
        self._synchronous = synchronous
        self._unsubs.append(self.event_bus.subscribe(
            self.REQUEST_EVENT, self._on_local_request,
        ))
        self._unsubs.append(self.event_bus.subscribe(
            self.INBOUND_EVENT, self._on_resolved,
        ))
        self._stop.clear()
        if not synchronous:
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="roll-request-worker",
                daemon=True,
            )
            self._worker.start()
        self._reaper = threading.Thread(
            target=self._reap_loop, name="roll-request-reaper", daemon=True,
        )
        self._reaper.start()
        logger.info(
            "roll dispatcher started (timeout=%.1fs on_timeout=%s synchronous=%s)",
            self.timeout_s, self.on_timeout, synchronous,
        )

    def stop(self) -> None:
        self._stop.set()
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass
        self._unsubs.clear()
        # Wake the worker so it exits promptly.
        try:
            self._work_queue.put_nowait(None)
        except Exception:  # noqa: BLE001
            pass
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None
        if self._reaper is not None:
            self._reaper.join(timeout=1.0)
            self._reaper = None

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Block until the worker has drained its queue (test helper)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._work_queue.empty():
                # Tiny grace so the worker can finish its current
                # ``_finalise`` call after popping the last item.
                time.sleep(0.02)
                if self._work_queue.empty():
                    return True
            time.sleep(0.02)
        return False

    # ------------------------------------------------------------------ #
    # Public API used by Director / triggers
    # ------------------------------------------------------------------ #

    def emit_from_dice_request(
        self,
        req: DiceRequest,
        *,
        actor_id: str | None,
        scene_id: str | None,
        correlation: dict[str, Any] | None = None,
    ) -> str | None:
        """Translate an LLM-emitted :class:`DiceRequest` into an
        outbound ``request_player_roll`` event. Returns the request id
        on success, ``None`` if disabled / invalid.
        """
        if not self.enabled:
            return None
        roll_type = (req.type or "skill").lower()
        key = req.skill or None
        request_id = req.request_id or f"prq-{uuid.uuid4().hex[:12]}"
        prompt = req.prompt_text or self._default_prompt(roll_type, key, req.dc)
        # Default formula: 1d20 (modifier resolution happens client-side
        # if the actor sheet has a stat for it, otherwise the player
        # supplies it via the roll card).
        formula = req.formula or "1d20"
        advantage = (req.advantage or "normal").lower()
        visibility = (req.visibility or "public").lower()
        target_actor = req.actor_id or req.actor or actor_id

        payload = {
            "request_id": request_id,
            "actor_id": target_actor,
            "roll_type": roll_type,
            "key": key,
            "dc": req.dc,
            "ac": req.ac,
            "advantage": advantage,
            "formula": formula,
            "prompt_text": prompt,
            "visibility": visibility,
            "expires_in_s": int(self.timeout_s),
            "reason": req.reason,
        }
        return self._emit(payload, target_actor=target_actor, scene_id=scene_id,
                          correlation=correlation or {})

    def emit_from_dice_requests(
        self,
        requests: list[DiceRequest],
        *,
        actor_id: str | None,
        scene_id: str | None,
        correlation: dict[str, Any] | None = None,
    ) -> list[str]:
        out: list[str] = []
        for req in requests or []:
            try:
                rid = self.emit_from_dice_request(
                    req, actor_id=actor_id, scene_id=scene_id, correlation=correlation,
                )
                if rid:
                    out.append(rid)
            except Exception as exc:  # noqa: BLE001
                logger.warning("emit_from_dice_request failed: %s", exc)
        return out

    # ------------------------------------------------------------------ #
    # Event-bus handlers
    # ------------------------------------------------------------------ #

    def _on_local_request(self, payload: dict[str, Any]) -> None:
        """Handler for internally published ``roll.requested`` events.

        Lets triggers / planner code request a player roll without
        owning a reference to the dispatcher.
        """
        try:
            self._emit(
                payload,
                target_actor=payload.get("actor_id"),
                scene_id=payload.get("scene_id"),
                correlation=payload.get("correlation") or {},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("local roll.requested handling failed: %s", exc)

    def _on_resolved(self, payload: dict[str, Any]) -> None:
        """Recv-thread entrypoint: validate + enqueue, never block.

        The full ``_finalise`` chain (publish ``roll.resolved`` →
        downstream subscribers like CombatProjector dispatch Foundry
        commands) MUST run off the websocket recv thread; otherwise
        the apply_damage response gets queued behind us and we
        deadlock for ``timeout_s``. See module docstring.
        """
        rid = payload.get("request_id")
        if not rid or not isinstance(rid, str):
            logger.warning("player_roll_resolved missing request_id: %s", payload)
            return
        with self._lock:
            pending = self._pending.pop(rid, None)
        if pending is None:
            logger.info("player_roll_resolved for unknown id %s (timed-out?)", rid)
            return
        record = self._record_from_resolved(pending, payload)
        self._schedule_finalise(pending, record, source="player")

    def _schedule_finalise(
        self, pending: _Pending, record: RollRecord, *, source: str,
    ) -> None:
        if self._synchronous or self._worker is None:
            self._finalise(pending, record, source=source)
            return
        try:
            self._work_queue.put_nowait((pending, record, source))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "roll dispatcher: enqueue failed (running inline as fallback): %s",
                exc,
            )
            self._finalise(pending, record, source=source)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._work_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                return
            pending, record, source = item
            try:
                self._finalise(pending, record, source=source)
            except Exception:  # noqa: BLE001
                logger.exception("roll-request worker raised; dropping resolution")

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _emit(
        self,
        payload: dict[str, Any],
        *,
        target_actor: str | None,
        scene_id: str | None,
        correlation: dict[str, Any],
    ) -> str | None:
        rid = payload.get("request_id") or f"prq-{uuid.uuid4().hex[:12]}"
        payload = {**payload, "request_id": rid}
        pending = _Pending(
            request_id=rid,
            actor_id=target_actor,
            scene_id=scene_id,
            roll_type=str(payload.get("roll_type") or "skill"),
            key=(payload.get("key") or None),
            dc=payload.get("dc"),
            ac=payload.get("ac"),
            formula=payload.get("formula"),
            advantage=str(payload.get("advantage") or "normal"),
            visibility=str(payload.get("visibility") or "public"),
            prompt_text=str(payload.get("prompt_text") or ""),
            reason=payload.get("reason"),
            issued_at=time.monotonic(),
            expires_at=time.monotonic() + self.timeout_s,
            correlation=dict(correlation),
        )
        with self._lock:
            self._pending[rid] = pending
        if self.client is None:
            logger.info("no foundry client wired — roll request %s queued only", rid)
            return rid
        try:
            self.client.send_event("request_player_roll", payload)
            logger.info(
                "request_player_roll dispatched: id=%s actor=%s type=%s key=%s dc=%s",
                rid, target_actor, pending.roll_type, pending.key, pending.dc,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("send request_player_roll failed: %s", exc)
            with self._lock:
                self._pending.pop(rid, None)
            return None
        return rid

    def _default_prompt(self, roll_type: str, key: str | None, dc: int | None) -> str:
        if roll_type == "skill" and key:
            base = f"Make a {key.title()} check"
        elif roll_type == "save" and key:
            base = f"Make a {key.upper()} saving throw"
        elif roll_type == "ability" and key:
            base = f"Make a {key.upper()} check"
        elif roll_type == "attack":
            base = "Roll to attack"
        elif roll_type == "damage":
            base = "Roll damage"
        else:
            base = "Make a roll"
        if dc is not None:
            base += f" (DC {dc})"
        return base

    def _record_from_resolved(
        self, pending: _Pending, payload: dict[str, Any],
    ) -> RollRecord:
        return RollRecord(
            request_id=pending.request_id,
            source="player",
            actor_id=pending.actor_id or payload.get("actor_id"),
            roll_type=pending.roll_type,
            key=pending.key,
            formula=str(payload.get("formula") or pending.formula or "1d20"),
            total=int(payload.get("total") or 0),
            modifier=int(payload.get("modifier") or 0),
            rolls=list(payload.get("rolls") or payload.get("dice") or []),
            kept=list(payload.get("kept") or []),
            advantage=str(payload.get("advantage") or pending.advantage),
            crit=bool(payload.get("crit")),
            fumble=bool(payload.get("fumble")),
            dc=pending.dc,
            ac=pending.ac,
            success=payload.get("success"),
            visibility=pending.visibility,
            scene_id=pending.scene_id,
            reason=pending.reason,
            prompt_text=pending.prompt_text,
            elapsed_ms=int((time.monotonic() - pending.issued_at) * 1000),
        )

    def _finalise(self, pending: _Pending, record: RollRecord, *, source: str) -> None:
        if self.roll_log is not None:
            self.roll_log.append(record)
        try:
            self.event_bus.publish(self.RESOLVED_EVENT, {
                "record": record.to_dict(),
                "source": source,
                # Forward correlation so subscribers (XPAwarder /
                # InteractionEffectsApplier) can apply authored
                # consequences without needing a back-reference to
                # the dispatcher's _Pending entry.
                "correlation": dict(pending.correlation or {}),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("publish roll.resolved failed: %s", exc)
        # Resume the narration loop with a synthetic player input that
        # carries the outcome. PlayerInputDispatcher already has
        # threading + per-actor session handling, so reusing it gets
        # the planner state, NPC memory, and prompt context for free.
        if pending.actor_id:
            self._enqueue_followup(pending, record)

    def _enqueue_followup(self, pending: _Pending, record: RollRecord) -> None:
        outcome_word: str
        if record.success is True:
            outcome_word = "success"
        elif record.success is False:
            outcome_word = "failure"
        else:
            outcome_word = "n/a"
        target = (
            f"vs DC {record.dc}" if record.dc is not None
            else (f"vs AC {record.ac}" if record.ac is not None else "")
        )
        text = (
            f"[roll-result {record.source}] "
            f"{record.roll_type}{('/' + record.key) if record.key else ''} "
            f"= {record.total} {target} → {outcome_word}"
            + (" CRIT" if record.crit else (" FUMBLE" if record.fumble else ""))
        ).strip()

        # Splice in the authored interaction context (when the request
        # came from IntentRouter._maybe_request_authored_roll). Without
        # this the LLM sees only the bare roll line and typically
        # produces a one-line acknowledgement — so the player
        # experiences "I rolled, nothing happened" even on a clean
        # success at an authored interactable.
        corr = pending.correlation or {}
        feature = corr.get("feature")
        verb = corr.get("verb")
        raw = corr.get("raw_text")
        summary = corr.get("summary")
        on_success = corr.get("on_success")
        on_failure = corr.get("on_failure")
        grants = corr.get("grants")
        starts_enc = corr.get("starts_encounter")
        ends_enc = corr.get("ends_encounter")
        ends_scene = corr.get("ends_scene")

        ctx_bits: list[str] = []
        if verb and feature:
            ctx_bits.append(f"action: {verb} {feature}")
        elif feature:
            ctx_bits.append(f"target: {feature}")
        if raw:
            ctx_bits.append(f'said: "{raw}"')
        if summary:
            ctx_bits.append(f"interaction: {summary}")
        # Surface only the relevant branch so the LLM doesn't see
        # contradictory authored text (e.g. both success and failure
        # consequences on the same turn).
        if record.success is True and on_success:
            ctx_bits.append(f"authored on_success: {on_success}")
            if grants:
                ctx_bits.append(f"grants: {grants}")
            if starts_enc:
                ctx_bits.append(f"starts_encounter: {starts_enc}")
            if ends_enc:
                ctx_bits.append(f"ends_encounter: {ends_enc}")
            if ends_scene:
                ctx_bits.append(f"ends_scene: {ends_scene}")
        elif record.success is False and on_failure:
            ctx_bits.append(f"authored on_failure: {on_failure}")
        if ctx_bits:
            text = text + "\n  " + "\n  ".join(ctx_bits)
            text += (
                "\n  Narrate the consequence above for the player. Quote"
                " or closely paraphrase the authored line; do not invent"
                " loot, encounters, or scene changes that aren't listed."
            )

        synthetic = {
            "text": text,
            "actor_id": pending.actor_id,
            "scene_id": pending.scene_id,
            "user_id": corr.get("user_id"),
            "user_name": corr.get("user_name"),
            "actor_name": corr.get("actor_name"),
            "origin": "roll_request_dispatcher",
            "roll_request_id": pending.request_id,
        }
        try:
            self.event_bus.publish("foundry.player_input", synthetic)
            logger.info(
                "follow-up turn enqueued for roll %s: %s",
                pending.request_id, text,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("follow-up player_input publish failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Reaper / timeout handling
    # ------------------------------------------------------------------ #

    def _reap_loop(self) -> None:
        while not self._stop.wait(1.0):
            now = time.monotonic()
            expired: list[_Pending] = []
            with self._lock:
                for rid, p in list(self._pending.items()):
                    if p.expires_at <= now:
                        expired.append(p)
                        del self._pending[rid]
            for p in expired:
                try:
                    self._handle_timeout(p)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("timeout handling for %s failed: %s", p.request_id, exc)

    def _handle_timeout(self, pending: _Pending) -> None:
        logger.info(
            "roll request %s timed out after %.1fs (policy=%s)",
            pending.request_id, self.timeout_s, self.on_timeout,
        )
        if self.on_timeout == "cancel":
            try:
                self.event_bus.publish("roll.cancelled", {
                    "request_id": pending.request_id,
                    "actor_id": pending.actor_id,
                    "reason": "timeout",
                })
            except Exception:  # noqa: BLE001
                pass
            return
        if self.on_timeout == "gm_prompt":
            if self.client is not None:
                try:
                    self.client.send_event("request_gm_roll", {
                        "request_id": pending.request_id,
                        "actor_id": pending.actor_id,
                        "roll_type": pending.roll_type,
                        "key": pending.key,
                        "dc": pending.dc,
                        "ac": pending.ac,
                        "prompt_text": pending.prompt_text,
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.warning("request_gm_roll send failed: %s", exc)
            return
        # Default: auto-roll server-side.
        if self.dm_roller is None:
            logger.warning(
                "auto_roll requested but DMRoller not wired — dropping %s",
                pending.request_id,
            )
            return
        record = self.dm_roller.roll(
            actor_id=pending.actor_id,
            roll_type=pending.roll_type,
            key=pending.key,
            formula=(None if pending.roll_type in ("skill", "save", "ability", "attack")
                     else (pending.formula or "1d20")),
            modifier=0,
            dc=pending.dc,
            ac=pending.ac,
            advantage=pending.advantage,  # type: ignore[arg-type]
            visibility="gm",
            scene_id=pending.scene_id,
            reason=(pending.reason or "auto-roll (player did not respond)"),
            request_id=pending.request_id,
        )
        # Treat the auto-roll like a player resolution for downstream
        # purposes (resume the narration loop), but flag the source.
        record.source = "dm-auto"
        if self.roll_log is not None:
            # Already logged by DMRoller; rewrite the source tag so
            # downstream queries can tell auto-rolls apart. Append a
            # marker entry rather than mutating the previous line.
            marker = RollRecord(
                request_id=pending.request_id,
                source="dm-auto",
                actor_id=pending.actor_id,
                roll_type=pending.roll_type,
                key=pending.key,
                formula=record.formula,
                total=record.total,
                modifier=record.modifier,
                rolls=list(record.rolls),
                kept=list(record.kept),
                advantage=record.advantage,
                crit=record.crit,
                fumble=record.fumble,
                dc=record.dc,
                ac=record.ac,
                success=record.success,
                visibility="gm",
                scene_id=pending.scene_id,
                reason="auto-roll on timeout",
                prompt_text=pending.prompt_text,
            )
            self.roll_log.append(marker)
        self._schedule_finalise(pending, record, source="dm-auto")

