"""Reconciler: diff our internal models against live Foundry state.

Walks every registered actor / token, pulls the live snapshot via
:class:`SyncService`, and produces a :class:`Diff`. ``auto_heal`` then
emits the minimum set of commands (HP patch, token move) to make the
two sides agree, dispatched through the :class:`CommandRouter`.

Reconciliation is opt-in (driven by triggers or explicit calls) so it
doesn't add latency to every game turn.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ai_dm.ai.schemas import Command as AICommand
from ai_dm.foundry.registry import FoundryRegistry
from ai_dm.foundry.snapshots import ActorSnapshot, SceneSnapshot
from ai_dm.foundry.sync_service import SyncService
from ai_dm.orchestration.event_bus import EventBus

logger = logging.getLogger("ai_dm.foundry.reconciler")


@dataclass
class Diff:
    actors: dict[str, dict[str, Any]] = field(default_factory=dict)
    tokens: dict[str, dict[str, Any]] = field(default_factory=dict)
    missing_actors: list[str] = field(default_factory=list)
    missing_tokens: list[str] = field(default_factory=list)

    def is_clean(self) -> bool:
        return not (self.actors or self.tokens or self.missing_actors or self.missing_tokens)

    def to_dict(self) -> dict:
        return {
            "actors": dict(self.actors),
            "tokens": dict(self.tokens),
            "missing_actors": list(self.missing_actors),
            "missing_tokens": list(self.missing_tokens),
        }


class Reconciler:
    def __init__(
        self,
        *,
        sync: SyncService,
        registry: FoundryRegistry,
        event_bus: EventBus | None = None,
        command_router=None,
        actor_state_provider=None,  # callable(actor_id) -> dict | None
    ) -> None:
        self.sync = sync
        self.registry = registry
        self.event_bus = event_bus
        self.command_router = command_router
        self.actor_state_provider = actor_state_provider

    # ------------------------------------------------------------------ #

    def run(self) -> Diff:
        diff = Diff()
        for entry in self.registry.all("actor"):
            try:
                snap = self.sync.pull_actor(entry.foundry_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("pull_actor %s failed: %s", entry.foundry_id, exc)
                diff.missing_actors.append(entry.foundry_id)
                continue
            if snap is None:
                diff.missing_actors.append(entry.foundry_id)
                continue
            actor_diff = self._diff_actor(entry.foundry_id, snap)
            if actor_diff:
                diff.actors[entry.foundry_id] = actor_diff

        if self.event_bus is not None:
            try:
                self.event_bus.publish("sync.diff_detected", diff.to_dict())
            except Exception:  # noqa: BLE001
                pass
        return diff

    def auto_heal(self, diff: Diff) -> int:
        """Push minimal commands to bring Foundry in line with local state.

        Returns the number of commands dispatched.
        """
        if self.command_router is None:
            return 0
        cmds: list[AICommand] = []
        for actor_id, change in diff.actors.items():
            patch = change.get("patch") or {}
            if patch:
                cmds.append(AICommand(type="update_actor", actor_id=actor_id, patch=patch))
        if not cmds:
            return 0
        try:
            self.command_router.dispatch(cmds)
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto_heal dispatch failed: %s", exc)
            return 0
        return len(cmds)

    # ------------------------------------------------------------------ #

    def _diff_actor(self, actor_id: str, snap: ActorSnapshot) -> dict[str, Any] | None:
        if self.actor_state_provider is None:
            return None
        local = None
        try:
            local = self.actor_state_provider(actor_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("actor_state_provider failed for %s: %s", actor_id, exc)
            return None
        if not local:
            return None
        patch: dict[str, Any] = {}
        if "hp" in local and snap.hp is not None and local["hp"] != snap.hp:
            patch["system.attributes.hp.value"] = local["hp"]
        if "name" in local and snap.name and local["name"] != snap.name:
            patch["name"] = local["name"]
        if not patch:
            return None
        return {"patch": patch, "remote": snap.model_dump(by_alias=True)}
