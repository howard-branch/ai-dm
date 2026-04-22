"""Long-form story planner.

Subscribes to gameplay events (``combat.encounter_ended``, ``trigger.fired``,
``intent.resolved``) to advance the arc. Exposes ``next_beat`` and
``summary_for_prompt`` for the narrator's context builder.
"""
from __future__ import annotations

import logging
from typing import Iterable

from ai_dm.ai.arc_schemas import ArcState, Beat, Chapter, Scene, StoryThread
from ai_dm.game.timeline import Timeline
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.services.chapter_service import ChapterService

logger = logging.getLogger("ai_dm.planner")


class StoryPlanner:
    def __init__(
        self,
        *,
        chapters: ChapterService,
        timeline: Timeline | None = None,
        event_bus: EventBus | None = None,
        initial_chapter: str | None = None,
    ) -> None:
        self.chapters = chapters
        self.timeline = timeline or Timeline()
        self.event_bus = event_bus
        self.state = ArcState(
            current_chapter=initial_chapter or chapters.first_id(),
        )
        if event_bus is not None:
            event_bus.subscribe("combat.encounter_ended", self._on_encounter_ended)
            event_bus.subscribe("intent.resolved", self._on_intent_resolved)
            event_bus.subscribe("chapter.advanced", self._on_chapter_advanced)

    # ---------- Public read API used by the narrator ---------- #

    def current_chapter(self) -> Chapter | None:
        cid = self.state.current_chapter
        return self.chapters.get(cid) if cid else None

    def current_scene(self) -> Scene | None:
        chap = self.current_chapter()
        if chap is None:
            return None
        if self.state.current_scene:
            for s in chap.scenes:
                if s.id == self.state.current_scene:
                    return s
        return chap.scenes[0] if chap.scenes else None

    def next_beat(self) -> Beat | None:
        scene = self.current_scene()
        if scene is None:
            return None
        for beat in scene.beats:
            if beat.id not in self.state.beats_completed:
                return beat
        return None

    def summary_for_prompt(self, *, max_chars: int = 600) -> str:
        chap = self.current_chapter()
        if chap is None:
            return ""
        bits = [f"# Chapter: {chap.title}"]
        if chap.summary:
            bits.append(chap.summary.strip())
        scene = self.current_scene()
        if scene:
            bits.append(f"## Scene: {scene.name or scene.id}")
            if scene.summary:
                bits.append(scene.summary.strip())
        beat = self.next_beat()
        if beat:
            bits.append(f"## Next beat: {beat.summary or beat.id}")
        threads = [t for t in self.state.threads if t.status in ("open", "active")]
        if threads:
            bits.append("## Open threads:\n" + "\n".join(f"- {t.summary}" for t in threads))
        joined = "\n\n".join(bits)
        return joined if len(joined) <= max_chars else joined[: max_chars - 1] + "…"

    # ---------- Mutators ---------- #

    def advance(self, beat_id: str, *, reason: str = "") -> None:
        if beat_id in self.state.beats_completed:
            return
        self.state.beats_completed.append(beat_id)
        self.timeline.record("beat_completed", reason or beat_id, {"beat_id": beat_id})
        self._publish("beat.completed", {"beat_id": beat_id, "reason": reason})

    def advance_chapter(self, chapter_id: str | None = None, *, reason: str = "") -> None:
        if chapter_id is None:
            chapter_id = self._next_chapter_id()
        if chapter_id is None:
            return
        prev = self.state.current_chapter
        self.state.current_chapter = chapter_id
        self.state.current_scene = None
        self.timeline.record("chapter_advanced", reason or chapter_id, {"from": prev, "to": chapter_id})
        self._publish("chapter.advanced", {"chapter_id": chapter_id, "from": prev, "reason": reason})

    def enter_scene(self, scene_id: str) -> None:
        prev = self.state.current_scene
        self.state.current_scene = scene_id
        self.timeline.record("scene_entered", scene_id, {"from": prev})
        self._publish("scene.entered", {"scene_id": scene_id, "from": prev})

    def open_thread(self, thread_id: str, summary: str = "") -> StoryThread:
        for t in self.state.threads:
            if t.id == thread_id:
                return t
        thread = StoryThread(id=thread_id, summary=summary, status="open")
        self.state.threads.append(thread)
        self._publish("arc.thread_opened", {"thread_id": thread_id, "summary": summary})
        return thread

    def resolve_thread(self, thread_id: str) -> None:
        for t in self.state.threads:
            if t.id == thread_id:
                t.status = "resolved"
                self._publish("arc.thread_resolved", {"thread_id": thread_id})
                return

    # ---------- Persistence ---------- #

    def snapshot(self) -> dict:
        return {
            "arc": self.state.model_dump(),
            "timeline": self.timeline.snapshot(),
        }

    def restore(self, snap: dict | None) -> None:
        if not snap:
            return
        if "arc" in snap:
            self.state = ArcState.model_validate(snap["arc"])
        if "timeline" in snap:
            self.timeline.restore(snap["timeline"])

    # ---------- Event handlers ---------- #

    def _on_encounter_ended(self, payload: dict) -> None:
        eid = payload.get("encounter_id")
        self.timeline.record("encounter_ended", str(eid or "?"), payload)
        # If a beat declares completes_on this encounter id, mark it done.
        scene = self.current_scene()
        if scene is None:
            return
        for beat in scene.beats:
            cond = beat.completes_on or {}
            if cond.get("event") == "combat.encounter_ended" and cond.get("encounter_id") == eid:
                self.advance(beat.id, reason=f"defeated:{eid}")

    def _on_intent_resolved(self, payload: dict) -> None:
        intent = (payload or {}).get("intent") or {}
        kind = intent.get("type")
        if kind in ("speak", "interact", "use_item", "attack"):
            self.timeline.record("intent", str(intent.get("raw_text", kind))[:120], intent)

    def _on_chapter_advanced(self, payload: dict) -> None:
        # External (trigger-driven) advance; mirror it into our state.
        chapter_id = payload.get("chapter_id")
        if not chapter_id or chapter_id == self.state.current_chapter:
            return
        self.state.current_chapter = chapter_id
        self.state.current_scene = None

    # ---------- Internals ---------- #

    def _next_chapter_id(self) -> str | None:
        ids = [c.id for c in self.chapters.all()]
        if not ids:
            return None
        cur = self.state.current_chapter
        if cur is None or cur not in ids:
            return ids[0]
        idx = ids.index(cur) + 1
        return ids[idx] if idx < len(ids) else None

    def _publish(self, name: str, payload: dict) -> None:
        if self.event_bus is None:
            return
        try:
            self.event_bus.publish(name, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("planner publish %s failed: %s", name, exc)


# ---- Backwards-compat trivial Planner kept for old callers ---- #

class Planner:
    def choose_next_scene(self, context: dict) -> str | None:
        return None
