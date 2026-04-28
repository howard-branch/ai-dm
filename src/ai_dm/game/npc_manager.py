"""NPC / monster registry.

Holds two distinct things:

* A library of immutable :class:`StatBlock` definitions keyed by
  ``stat_block.key`` (the SRD-style "monster manual" entry).
* A registry of *spawned* :class:`CombatantState` instances keyed by
  their ``actor_id`` (the per-encounter slice).

This is intentionally tiny and synchronous — persistence,
event-bus wiring and Foundry mirroring belong to higher layers. The
manager only guarantees the in-memory invariants (unique ids, spawn
hydration, deregistration on despawn).
"""
from __future__ import annotations

from typing import Any, Iterable

from ai_dm.game.combatant_state import CombatantState
from ai_dm.game.monster_state import StatBlock


class NPCManager:
    """In-memory registry of stat blocks and spawned NPCs."""

    def __init__(self) -> None:
        self._stat_blocks: dict[str, StatBlock] = {}
        self._spawned: dict[str, CombatantState] = {}

    # ------------------------------------------------------------------ #
    # Stat-block library
    # ------------------------------------------------------------------ #

    def register_stat_block(self, block: StatBlock | dict[str, Any]) -> StatBlock:
        sb = block if isinstance(block, StatBlock) else StatBlock.model_validate(block)
        self._stat_blocks[sb.key] = sb
        return sb

    def register_many(self, blocks: Iterable[StatBlock | dict[str, Any]]) -> int:
        n = 0
        for b in blocks:
            self.register_stat_block(b)
            n += 1
        return n

    def get_stat_block(self, key: str) -> StatBlock | None:
        return self._stat_blocks.get(key)

    def stat_blocks(self) -> list[StatBlock]:
        return list(self._stat_blocks.values())

    # ------------------------------------------------------------------ #
    # Spawned NPCs
    # ------------------------------------------------------------------ #

    def spawn(
        self,
        stat_block_key: str,
        *,
        actor_id: str,
        token_id: str | None = None,
        team: str = "foe",
        position: dict[str, Any] | None = None,
    ) -> CombatantState:
        """Hydrate a fresh combatant from a registered stat block."""
        sb = self._stat_blocks.get(stat_block_key)
        if sb is None:
            raise KeyError(f"unknown stat_block {stat_block_key!r}")
        if actor_id in self._spawned:
            raise ValueError(f"actor_id {actor_id!r} already spawned")
        c = sb.to_combatant(
            actor_id=actor_id, token_id=token_id, team=team, position=position,
        )
        self._spawned[actor_id] = c
        return c

    def despawn(self, actor_id: str) -> CombatantState | None:
        return self._spawned.pop(actor_id, None)

    def get_npc(self, actor_id: str) -> CombatantState | None:
        return self._spawned.get(actor_id)

    def spawned(self) -> list[CombatantState]:
        return list(self._spawned.values())

    # ------------------------------------------------------------------ #
    # Persistence helpers
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict[str, Any]:
        return {
            "stat_blocks": [sb.model_dump() for sb in self._stat_blocks.values()],
            "spawned": [c.model_dump() for c in self._spawned.values()],
        }

    def restore(self, blob: dict[str, Any]) -> None:
        self._stat_blocks = {}
        self._spawned = {}
        for raw in blob.get("stat_blocks") or []:
            self.register_stat_block(raw)
        for raw in blob.get("spawned") or []:
            c = CombatantState.model_validate(raw)
            self._spawned[c.actor_id] = c
