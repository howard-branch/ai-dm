"""Authority arbitration for Foundry inbound events.

**Authority model: Python is authoritative.**

Foundry is treated as a rendering / execution layer. Inbound mutations
(GMs dragging tokens, manual HP edits, scene swaps) are *external* and
need a per-event-type policy:

    absorb   — accept the change into Python state (default)
    revert   — push the inverse command back so Foundry matches Python
    ignore   — log only

Every outbound write we send is fingerprinted into an
:class:`EchoSuppressor` (TTL ~ a few seconds). When the matching inbound
echo arrives we tag it as ``self_echo`` and skip the policy entirely —
otherwise we'd ping-pong with our own writes.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from ai_dm.ai.schemas import Command as AICommand
from ai_dm.foundry.registry import FoundryRegistry
from ai_dm.orchestration.event_bus import EventBus

logger = logging.getLogger("ai_dm.foundry.authority")

Policy = Literal["absorb", "revert", "ignore"]


# ---------------------------------------------------------------------- #
# Echo suppression
# ---------------------------------------------------------------------- #

@dataclass
class _Echo:
    fingerprint: str
    expires_at: float


class EchoSuppressor:
    """Bounded TTL set of fingerprints of recent outbound writes."""

    def __init__(self, *, ttl: float = 3.0, max_entries: int = 256) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: deque[_Echo] = deque()

    def remember(self, fingerprint: str) -> None:
        if not fingerprint:
            return
        now = time.monotonic()
        with self._lock:
            self._gc(now)
            self._entries.append(_Echo(fingerprint, now + self.ttl))
            while len(self._entries) > self.max_entries:
                self._entries.popleft()

    def consume(self, fingerprint: str) -> bool:
        """Return True (and drop the entry) if ``fingerprint`` is suppressed."""
        if not fingerprint:
            return False
        now = time.monotonic()
        with self._lock:
            self._gc(now)
            for i, ent in enumerate(self._entries):
                if ent.fingerprint == fingerprint:
                    del self._entries[i]
                    return True
        return False

    def _gc(self, now: float) -> None:
        while self._entries and self._entries[0].expires_at < now:
            self._entries.popleft()

    def fingerprint_command(self, cmd: AICommand | dict) -> str | None:
        """Map an outbound command to the fingerprint its echo will carry."""
        if hasattr(cmd, "model_dump"):
            data = cmd.model_dump()  # type: ignore[union-attr]
        elif isinstance(cmd, dict):
            data = cmd
        else:
            return None
        ctype = data.get("type")
        if ctype == "move_token":
            return _fp("token_moved", data.get("token_id"), data.get("x"), data.get("y"))
        if ctype == "update_actor":
            patch = data.get("patch") or {}
            hp = _hp_from_patch(patch)
            if hp is not None:
                return _fp("actor_hp", data.get("actor_id"), hp)
            return _fp("actor_updated", data.get("actor_id"), sorted(patch.keys()))
        if ctype == "activate_scene":
            return _fp("scene_activated", data.get("scene_id"))
        return None

    def fingerprint_inbound(self, event: str, payload: dict) -> str | None:
        """Map an inbound foundry.* payload to the same fingerprint."""
        if event == "token_moved":
            return _fp("token_moved", payload.get("token_id"), payload.get("x"), payload.get("y"))
        if event == "actor_updated":
            hp = payload.get("hp")
            if hp is not None:
                return _fp("actor_hp", payload.get("actor_id"), hp)
            change = payload.get("change") or {}
            return _fp("actor_updated", payload.get("actor_id"), sorted(change.keys()))
        if event == "scene_activated":
            return _fp("scene_activated", payload.get("scene_id"))
        return None


def _fp(*parts: Any) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


def _hp_from_patch(patch: dict) -> Any:
    # Accept both flat and dotted forms.
    if "hp" in patch:
        return patch["hp"]
    return patch.get("system.attributes.hp.value")


# ---------------------------------------------------------------------- #
# Arbitration
# ---------------------------------------------------------------------- #

@dataclass
class AuthorityPolicy:
    """Per-event policy. Defaults: Python authoritative + absorb everything.

    Set an entry to ``"revert"`` to make Python actively push the
    inverse command back to Foundry whenever a GM mutates that surface
    behind our back.
    """

    token_moved: Policy = "absorb"
    actor_updated: Policy = "absorb"
    scene_activated: Policy = "absorb"
    default: Policy = "ignore"

    def for_event(self, event: str) -> Policy:
        return getattr(self, event, self.default)


@dataclass
class ArbitrationOutcome:
    event: str
    policy: Policy
    self_echo: bool = False
    absorbed: bool = False
    reverted: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "event": self.event,
            "policy": self.policy,
            "self_echo": self.self_echo,
            "absorbed": self.absorbed,
            "reverted": self.reverted,
            "reason": self.reason,
        }


class InboundArbiter:
    """Sole consumer of ``foundry.*`` inbound events.

    Subscribes to the EventBus, classifies each inbound event against the
    :class:`EchoSuppressor` + :class:`AuthorityPolicy`, then absorbs the
    change into Python state (or pushes the inverse) and re-publishes
    a structured ``authority.*`` event for downstream listeners.
    """

    def __init__(
        self,
        *,
        event_bus: EventBus,
        suppressor: EchoSuppressor,
        registry: FoundryRegistry,
        policy: AuthorityPolicy | None = None,
        actor_state: dict | None = None,
        token_state: dict | None = None,
        scene_state: dict | None = None,
        command_router: Any = None,
    ) -> None:
        self.event_bus = event_bus
        self.suppressor = suppressor
        self.registry = registry
        self.policy = policy or AuthorityPolicy()
        self.actor_state: dict = actor_state if actor_state is not None else {}
        self.token_state: dict = token_state if token_state is not None else {}
        self.scene_state: dict = scene_state if scene_state is not None else {}
        self.command_router = command_router
        self._unsubs: list[Callable[[], None]] = []
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._unsubs:
            return
        for ev in ("token_moved", "actor_updated", "scene_activated"):
            self._unsubs.append(
                self.event_bus.subscribe(f"foundry.{ev}", self._make_handler(ev))
            )

    def stop(self) -> None:
        for u in self._unsubs:
            try:
                u()
            except Exception:  # noqa: BLE001
                pass
        self._unsubs.clear()

    # ------------------------------------------------------------------ #
    # Outbound hook (priming)
    # ------------------------------------------------------------------ #

    def prime_outbound(self, command: AICommand | dict) -> None:
        """Record the fingerprint of ``command`` so its echo is suppressed."""
        fp = self.suppressor.fingerprint_command(command)
        if fp:
            self.suppressor.remember(fp)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _make_handler(self, event: str) -> Callable[[dict], None]:
        def _h(payload: dict) -> None:
            try:
                self._handle(event, payload or {})
            except Exception as exc:  # noqa: BLE001
                logger.warning("arbiter %s failed: %s", event, exc)

        return _h

    def _handle(self, event: str, payload: dict) -> None:
        outcome = ArbitrationOutcome(event=event, policy=self.policy.for_event(event))

        fp = self.suppressor.fingerprint_inbound(event, payload)
        if fp and self.suppressor.consume(fp):
            outcome.self_echo = True
            outcome.reason = "self_echo"
            self._publish_outcome(outcome, payload)
            return

        if outcome.policy == "ignore":
            outcome.reason = "policy=ignore"
            self._publish_outcome(outcome, payload)
            return

        if outcome.policy == "absorb":
            self._absorb(event, payload)
            outcome.absorbed = True
            self._publish_outcome(outcome, payload)
            return

        if outcome.policy == "revert":
            ok = self._revert(event, payload)
            outcome.reverted = ok
            outcome.reason = "no_inverse" if not ok else ""
            self._publish_outcome(outcome, payload)
            return

    # ---- absorb -------------------------------------------------- #

    def _absorb(self, event: str, payload: dict) -> None:
        with self._lock:
            if event == "token_moved":
                tid = payload.get("token_id")
                if not tid:
                    return
                self.token_state[tid] = {
                    "x": payload.get("x"),
                    "y": payload.get("y"),
                    "scene_id": payload.get("scene_id"),
                }
            elif event == "actor_updated":
                aid = payload.get("actor_id")
                if not aid:
                    return
                cur = self.actor_state.setdefault(aid, {})
                if payload.get("hp") is not None:
                    cur["hp"] = payload["hp"]
                change = payload.get("change") or {}
                if isinstance(change, dict) and "name" in change:
                    cur["name"] = change["name"]
            elif event == "scene_activated":
                sid = payload.get("scene_id")
                self.scene_state["current_scene_id"] = sid
                self.scene_state["current_scene_name"] = payload.get("scene_name")

    # ---- revert -------------------------------------------------- #

    def _revert(self, event: str, payload: dict) -> bool:
        if self.command_router is None:
            logger.info("revert skipped (no command_router): %s", event)
            return False
        cmd: AICommand | None = None
        if event == "token_moved":
            tid = payload.get("token_id")
            prev = self.token_state.get(tid) if tid else None
            if not prev or prev.get("x") is None:
                return False
            cmd = AICommand(
                type="move_token",
                token_id=tid,
                x=int(prev["x"]),
                y=int(prev["y"]),
                scene_id=prev.get("scene_id"),
            )
        elif event == "actor_updated":
            aid = payload.get("actor_id")
            prev = self.actor_state.get(aid) if aid else None
            if not prev or prev.get("hp") is None or payload.get("hp") is None:
                return False
            if prev["hp"] == payload["hp"]:
                return False
            cmd = AICommand(
                type="update_actor",
                actor_id=aid,
                patch={"system.attributes.hp.value": prev["hp"]},
            )
        elif event == "scene_activated":
            prev_id = self.scene_state.get("current_scene_id")
            if not prev_id or prev_id == payload.get("scene_id"):
                return False
            cmd = AICommand(type="activate_scene", scene_id=prev_id)
        if cmd is None:
            return False
        # Prime the suppressor for our own corrective write.
        self.prime_outbound(cmd)
        try:
            outcome = self.command_router.dispatch([cmd])
            return bool(getattr(outcome, "ok", False))
        except Exception as exc:  # noqa: BLE001
            logger.warning("revert dispatch failed: %s", exc)
            return False

    # ------------------------------------------------------------------ #

    def _publish_outcome(self, outcome: ArbitrationOutcome, payload: dict) -> None:
        try:
            self.event_bus.publish(
                f"authority.{outcome.event}",
                {**outcome.to_dict(), "payload": payload},
            )
        except Exception:  # noqa: BLE001
            pass

