"""Builds the prompt context dict consumed by the narrator."""
from __future__ import annotations

from typing import Iterable, Protocol

from ai_dm.game.location_service import LocationService
from ai_dm.memory.npc_memory import NPCMemoryStore
from ai_dm.memory.relationships import RelationshipMatrix


class _StateLike(Protocol):
    def get_context(self) -> dict: ...


class PromptContextBuilder:
    def __init__(
        self,
        state_store: _StateLike | None,
        npc_memory: NPCMemoryStore | None = None,
        relationships: RelationshipMatrix | None = None,
        location_service: LocationService | None = None,
        story_planner=None,
        character: dict | None = None,
        party: list[dict] | None = None,
    ) -> None:
        self.state_store = state_store
        self.npc_memory = npc_memory
        self.relationships = relationships
        self.location_service = location_service
        self.story_planner = story_planner
        # Active player character sheet. The narrator uses this to know
        # who is acting / speaking. Update at runtime to swap PCs.
        self.character = character
        # Companion party members (AI-controlled or otherwise). Each entry
        # is a compact dict {id, name, class, level, hp, role, controller}
        # — enough for the narrator to answer "who is in my party".
        self.party: list[dict] = list(party or [])

    def build(
        self,
        player_input: str,
        *,
        focus_npcs: Iterable[str] | None = None,
        scene_id: str | None = None,
    ) -> dict:
        context: dict = {}
        if self.character:
            context["player_character"] = self.character
        if self.party:
            context["party"] = self.party
        if self.state_store is not None:
            try:
                context["state"] = self.state_store.get_context()
            except Exception:  # noqa: BLE001 — best-effort
                context["state"] = {}

        focus_list = list(focus_npcs or [])
        if focus_list and self.npc_memory is not None:
            briefs = []
            for npc_id in focus_list:
                briefs.append({
                    "npc_id": npc_id,
                    "summary": self.npc_memory.summary(npc_id),
                    "recent_events": [e.model_dump() for e in self.npc_memory.recent(npc_id)],
                    "facts": [e.model_dump() for e in self.npc_memory.facts(npc_id)],
                    "relationships": (
                        [r.model_dump() for r in self.relationships.for_subject(npc_id)]
                        if self.relationships is not None
                        else []
                    ),
                })
            context["npc_briefs"] = briefs

        if scene_id and self.location_service is not None:
            scene = self.location_service.get_scene(scene_id)
            if scene is not None:
                context["scene_locations"] = scene.model_dump()

        if self.story_planner is not None:
            try:
                hint = self.story_planner.summary_for_prompt()
                if hint:
                    context["arc_hint"] = hint
            except Exception:  # noqa: BLE001
                pass

        context["player_input"] = player_input
        return context

