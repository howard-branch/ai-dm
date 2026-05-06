"""Top-level campaign-state persistence.

A single JSON blob containing per-subsystem snapshots:
    - registry        (FoundryRegistry.snapshot)
    - locations       (LocationService.snapshot)
    - npc_memory      (NPCMemoryStore.snapshot)
    - relationships   (RelationshipMatrix.snapshot)
    - combat          (CombatMachine.snapshot)
    - flags           (key/value DM flags applied via state_updates)
    - session         (current session pointer)

A backup is rotated **before** every save. On load, ``_migrate`` upgrades
older payloads to the current ``SAVE_SCHEMA_VERSION``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_dm.foundry.registry import FoundryRegistry
from ai_dm.game.combat_machine import CombatMachine
from ai_dm.game.location_service import LocationService
from ai_dm.game.party_state import PartyState
from ai_dm.memory.npc_memory import NPCMemoryStore
from ai_dm.memory.relationships import RelationshipMatrix
from ai_dm.persistence.atomic_writer import atomic_write_json
from ai_dm.persistence.backups import BackupService
from ai_dm.persistence.file_lock import FileLock
from ai_dm.utils.time import now_iso

logger = logging.getLogger("ai_dm.persistence.campaign")

SAVE_SCHEMA_VERSION = 4
SAVE_FILENAME = "campaign_state.json"


class CampaignSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int = SAVE_SCHEMA_VERSION
    saved_at: str = Field(default_factory=now_iso)
    registry: dict = Field(default_factory=dict)
    locations: dict = Field(default_factory=dict)
    npc_memory: dict = Field(default_factory=dict)
    relationships: list = Field(default_factory=list)
    combat: dict | None = None
    flags: dict = Field(default_factory=dict)
    session: dict = Field(default_factory=dict)
    # Phase 3
    arc: dict = Field(default_factory=dict)
    timeline: list = Field(default_factory=list)
    fired_once_ids: list = Field(default_factory=list)
    foundry_journals: dict = Field(default_factory=dict)
    actor_state: dict = Field(default_factory=dict)
    clock: dict = Field(default_factory=dict)
    # Phase 5 (XP / party state).
    party: dict = Field(default_factory=dict)


@dataclass
class CampaignStore:
    base: Path
    registry: FoundryRegistry | None = None
    location_service: LocationService | None = None
    npc_memory: NPCMemoryStore | None = None
    relationships: RelationshipMatrix | None = None
    combat: CombatMachine | None = None
    backups: BackupService | None = None
    flags: dict[str, Any] | None = None
    session: dict[str, Any] | None = None
    # Phase 3 collaborators (all optional so older callers keep working).
    story_planner: Any = None
    triggers: Any = None
    foundry_journals: dict[str, Any] | None = None
    actor_state: dict[str, Any] | None = None
    clock: Any = None  # ai_dm.game.clock.Clock — typed loosely to avoid an import cycle
    # Phase 5: party / XP. Mutated in place so XPCollector and
    # InteractionEffectsApplier (which hold references) keep seeing
    # the live snapshot after a restore.
    party_state: PartyState | None = None

    def __post_init__(self) -> None:
        self.base = Path(self.base)
        self.base.mkdir(parents=True, exist_ok=True)
        if self.backups is None:
            self.backups = BackupService(self.base / "backups")
        if self.flags is None:
            self.flags = {}
        if self.session is None:
            self.session = {}
        if self.foundry_journals is None:
            self.foundry_journals = {}
        if self.actor_state is None:
            self.actor_state = {}

    @property
    def save_path(self) -> Path:
        return self.base / SAVE_FILENAME

    # ------------------------------------------------------------------ #
    # Save / Load
    # ------------------------------------------------------------------ #

    def collect(self) -> CampaignSnapshot:
        planner_snap = self.story_planner.snapshot() if self.story_planner else {}
        if not isinstance(planner_snap, dict):
            planner_snap = {}
        return CampaignSnapshot(
            registry=self.registry.snapshot() if self.registry else {},
            locations=self.location_service.snapshot() if self.location_service else {},
            npc_memory=self.npc_memory.snapshot() if self.npc_memory else {},
            relationships=self.relationships.snapshot() if self.relationships else [],
            combat=self.combat.snapshot() if self.combat else None,
            flags=dict(self.flags or {}),
            session=dict(self.session or {}),
            arc=planner_snap.get("arc", {}),
            timeline=planner_snap.get("timeline", []),
            fired_once_ids=list(self.triggers.snapshot()) if self.triggers else [],
            foundry_journals=dict(self.foundry_journals or {}),
            actor_state=dict(self.actor_state or {}),
            clock=self.clock.snapshot() if self.clock else {},
            party=self.party_state.model_dump() if self.party_state else {},
        )

    def save(self) -> Path:
        snapshot = self.collect()
        if self.backups is not None:
            self.backups.rotate(self.save_path)
        lock_path = self.save_path.with_suffix(self.save_path.suffix + ".lock")
        with FileLock(lock_path):
            atomic_write_json(self.save_path, snapshot.model_dump())
        logger.info("campaign saved to %s", self.save_path)
        return self.save_path

    def load(self) -> CampaignSnapshot:
        if not self.save_path.exists():
            return CampaignSnapshot()
        payload = json.loads(self.save_path.read_text(encoding="utf-8"))
        payload = self._migrate(payload)
        return CampaignSnapshot.model_validate(payload)

    def restore_into_runtime(self, snapshot: CampaignSnapshot | None = None) -> None:
        snap = snapshot or self.load()
        if self.registry is not None and snap.registry:
            self._restore_registry(snap.registry)
        if self.location_service is not None:
            self.location_service.restore(snap.locations)
        if self.npc_memory is not None:
            self.npc_memory.restore(snap.npc_memory)
        if self.relationships is not None:
            self.relationships.restore(snap.relationships)
        if self.combat is not None:
            self.combat.restore(snap.combat)
        if self.flags is not None:
            self.flags.clear()
            self.flags.update(snap.flags or {})
        if self.session is not None:
            self.session.clear()
            self.session.update(snap.session or {})
        if self.story_planner is not None:
            self.story_planner.restore({
                "arc": snap.arc or {},
                "timeline": snap.timeline or [],
            })
        if self.triggers is not None:
            self.triggers.restore(list(snap.fired_once_ids or []))
        if self.foundry_journals is not None:
            self.foundry_journals.clear()
            self.foundry_journals.update(snap.foundry_journals or {})
        if self.actor_state is not None:
            self.actor_state.clear()
            self.actor_state.update(snap.actor_state or {})
        if self.clock is not None and snap.clock:
            self.clock.restore(snap.clock)
        if self.party_state is not None and snap.party:
            # Mutate the existing instance in place so any subscriber
            # holding the original reference (XPCollector, …) keeps
            # the live state.
            restored = PartyState.model_validate(snap.party)
            self.party_state.members = list(restored.members)
            self.party_state.xp_pool = dict(restored.xp_pool)
            self.party_state.levels = dict(restored.levels)
            self.party_state.pending_xp = int(restored.pending_xp)
            self.party_state.xp_log = list(restored.xp_log)

    # ------------------------------------------------------------------ #

    def _restore_registry(self, snap: dict) -> None:
        assert self.registry is not None
        for kind in ("scene", "actor", "token"):
            for entry in snap.get(kind, []):
                self.registry.register(  # type: ignore[arg-type]
                    kind,
                    entry["foundry_id"],
                    name=entry["name"],
                    aliases=entry.get("aliases", ()),
                    scene_id=entry.get("scene_id"),
                )

    def _migrate(self, payload: dict) -> dict:
        version = int(payload.get("schema_version", 1))
        if version == SAVE_SCHEMA_VERSION:
            return payload
        if version == 1:
            payload = {
                "schema_version": 2,
                "saved_at": now_iso(),
                "registry": {},
                "locations": {},
                "npc_memory": {},
                "relationships": [],
                "combat": None,
                "flags": payload.get("flags", {}),
                "session": {},
            }
            version = 2
        if version == 2:
            payload = {
                **payload,
                "schema_version": 3,
                "arc": {},
                "timeline": [],
                "fired_once_ids": [],
                "foundry_journals": {},
                "actor_state": {},
            }
            version = 3
        if version == 3:
            payload = {
                **payload,
                "schema_version": 4,
                "party": {},
            }
            version = 4
        if version != SAVE_SCHEMA_VERSION:
            raise ValueError(f"unsupported save schema_version: {version}")
        return payload

