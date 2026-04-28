"""Builds the prompt context dict consumed by the narrator."""
from __future__ import annotations

from typing import Iterable, Protocol

from ai_dm.app.opening_scene import (
    build_scene_brief,
    find_chapter_scene,
    find_scene_node,
)
from ai_dm.campaign.pack import CampaignPack
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
        pack: CampaignPack | None = None,
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
        # Campaign pack — used to surface a per-turn scene brief
        # (interactables, exits, NPCs) so the narrator can keep the
        # player aware of their options on every turn, not just the
        # opening narration.
        self.pack = pack
        # Last scene id we successfully built a brief for. Used as a
        # fall-back when a turn arrives with a scene_id that doesn't
        # resolve in the pack (e.g. Foundry's opaque id vs. our slug).
        self._last_brief_scene: str | None = None

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

        # Resolve the caller's ``scene_id`` (often Foundry's opaque id
        # from chat events) to a campaign-pack scene id so downstream
        # lookups (`location_service.get_scene`, `build_scene_brief`)
        # actually find the *current* scene rather than silently
        # falling back to the manifest's start. The story planner is
        # the authoritative source: ``StoryPlanner.enter_scene`` is
        # invoked on every successful travel.
        pack_scene_id = self._resolve_pack_scene_id(scene_id)

        if pack_scene_id and self.location_service is not None:
            scene = self.location_service.get_scene(pack_scene_id)
            if scene is not None:
                context["scene_locations"] = scene.model_dump()

        # Per-turn scene brief: interactables / exits / NPCs available
        # right now. The narrator's system prompt instructs it to weave
        # at least one of these into its prose so the player keeps a
        # sense of available actions on every turn, not just at start.
        if self.pack is not None:
            brief = build_scene_brief(self.pack, pack_scene_id)
            # Only fall back to the previously-cached brief if we
            # couldn't resolve a pack scene at all this turn — never
            # when the planner has moved on. Otherwise scene 1's brief
            # would permanently shadow scene 2 after travel.
            if brief is None and not pack_scene_id and self._last_brief_scene:
                brief = build_scene_brief(self.pack, self._last_brief_scene)
            if brief is not None:
                context["scene_brief"] = brief
                self._last_brief_scene = brief.get("scene_id") or self._last_brief_scene

        if self.story_planner is not None:
            try:
                hint = self.story_planner.summary_for_prompt()
                if hint:
                    context["arc_hint"] = hint
            except Exception:  # noqa: BLE001
                pass

        context["player_input"] = player_input
        return context

    # ------------------------------------------------------------------ #

    def _resolve_pack_scene_id(self, scene_id: str | None) -> str | None:
        """Map ``scene_id`` (which is often Foundry's opaque id) to a
        pack-side scene node id. Resolution order:

        1. ``scene_id`` itself if it matches a pack node *or* a chapter
           scene id (``chapters/<chap>/scenes.json``). Chapter scene ids
           are the planner's currency after travel; treating them as
           valid here stops ``build_scene_brief`` from silently falling
           back to the manifest's start scene every turn.
        2. ``story_planner.state.current_scene`` — set by
           ``StoryPlanner.enter_scene`` on bootstrap and on every
           successful travel, so it tracks the player's *narrative*
           location even when chat events carry an opaque Foundry id.
        3. ``scene_id`` unchanged (let downstream callers decide).
        """
        if self.pack is not None and scene_id and (
            find_scene_node(self.pack, scene_id) is not None
            or find_chapter_scene(self.pack, scene_id) is not None
        ):
            return scene_id
        try:
            ps = getattr(self.story_planner, "state", None)
            cur = getattr(ps, "current_scene", None) if ps else None
            if cur:
                return cur
        except Exception:  # noqa: BLE001
            pass
        return scene_id

