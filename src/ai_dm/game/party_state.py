"""Party-level runtime state.

A small Pydantic v2 model representing the running party as a whole:
member ids, per-member XP / level, and a chronological award log.

Typical wiring: an :class:`ai_dm.game.encounter_manager.EncounterState`
asks :func:`ai_dm.rules.xp_budget.award_xp` to split a defeated-XP pool
across the party, then calls :meth:`PartyState.record_kill` for each
defeated foe and :meth:`PartyState.finalize_encounter` once.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_dm.rules import xp_budget


class XPAward(BaseModel):
    model_config = ConfigDict(extra="forbid")

    encounter_id: str | None = None
    monster_id: str | None = None
    cr: float | None = None
    raw_xp: int = 0
    awarded_per_member: int = 0
    timestamp: str | None = None


class PartyState(BaseModel):
    """Pooled state for the active adventuring party.

    ``xp_pool`` is a per-actor cumulative XP total; ``levels`` mirrors
    the level *implied* by that XP via
    :func:`ai_dm.rules.xp_budget.level_for_xp` but is stored explicitly
    so the rules engine doesn't have to recompute on every read and
    so player-visible level-up moments are observable.
    """

    model_config = ConfigDict(extra="forbid")

    members: list[str] = Field(default_factory=list)
    xp_pool: dict[str, int] = Field(default_factory=dict)
    levels: dict[str, int] = Field(default_factory=dict)
    pending_xp: int = 0  # accumulated this encounter, not yet awarded
    xp_log: list[XPAward] = Field(default_factory=list)
    schema_version: int = 1

    # ---- membership ------------------------------------------------ #

    def add_member(self, actor_id: str, *, level: int = 1, xp: int = 0) -> None:
        if actor_id not in self.members:
            self.members.append(actor_id)
        self.xp_pool.setdefault(actor_id, int(xp))
        self.levels.setdefault(actor_id, int(level))

    def remove_member(self, actor_id: str) -> None:
        if actor_id in self.members:
            self.members.remove(actor_id)
        self.xp_pool.pop(actor_id, None)
        self.levels.pop(actor_id, None)

    # ---- encounter integration ------------------------------------- #

    def record_kill(
        self,
        *,
        monster_id: str | None,
        cr: float | None,
        xp: int,
        encounter_id: str | None = None,
    ) -> XPAward:
        """Stage XP from a defeated monster into ``pending_xp``."""
        rec = XPAward(
            encounter_id=encounter_id,
            monster_id=monster_id,
            cr=cr,
            raw_xp=int(xp),
            awarded_per_member=0,
        )
        self.pending_xp += int(xp)
        self.xp_log.append(rec)
        return rec

    def finalize_encounter(self, encounter_id: str | None = None) -> dict[str, int]:
        """Distribute ``pending_xp`` across living party members.

        Returns the per-actor delta. Bumps ``levels`` if any member
        crosses an XP threshold.
        """
        if not self.members or self.pending_xp <= 0:
            self.pending_xp = 0
            return {m: 0 for m in self.members}
        per = xp_budget.award_xp(self.members, self.pending_xp)
        for actor_id, delta in per.items():
            new_total = self.xp_pool.get(actor_id, 0) + int(delta)
            self.xp_pool[actor_id] = new_total
            self.levels[actor_id] = max(
                self.levels.get(actor_id, 1),
                xp_budget.level_for_xp(new_total),
            )
        # Stamp award_per_member onto recent log entries from this encounter.
        even = self.pending_xp // len(self.members)
        for rec in self.xp_log:
            if rec.encounter_id == encounter_id and rec.awarded_per_member == 0:
                rec.awarded_per_member = even
        self.pending_xp = 0
        return per

    def level_up_pending(self, actor_id: str) -> bool:
        """``True`` if ``actor_id``'s XP exceeds the threshold for their
        current level + 1 (i.e. UI should prompt a level-up)."""
        cur_lvl = self.levels.get(actor_id, 1)
        cur_xp = self.xp_pool.get(actor_id, 0)
        return cur_xp >= xp_budget.xp_for_level(min(20, cur_lvl + 1))

    # ---- queries --------------------------------------------------- #

    def party_levels(self) -> list[int]:
        return [self.levels.get(m, 1) for m in self.members]

    def difficulty_for(self, monster_xps: list[int]) -> str:
        return xp_budget.classify_encounter(monster_xps, self.party_levels())


__all__ = ["PartyState", "XPAward"]

