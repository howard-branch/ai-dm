import json
from pathlib import Path

from ai_dm.ai.arc_schemas import Beat, Chapter, Scene
from ai_dm.ai.planner import StoryPlanner
from ai_dm.game.timeline import Timeline
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.services.chapter_service import ChapterService


def _build_chapter_service(tmp_path: Path) -> ChapterService:
    base = tmp_path / "chapters"
    chap_a = base / "chapter_01"
    chap_b = base / "chapter_02"
    chap_a.mkdir(parents=True)
    chap_b.mkdir(parents=True)
    (chap_a / "summary.md").write_text("Candlekeep awaits.")
    (chap_a / "scenes.json").write_text(json.dumps({
        "scenes": [
            {
                "id": "intro",
                "name": "Arrival",
                "summary": "The party arrives.",
                "beats": [
                    {"id": "meet_keeper", "summary": "Meet the keeper"},
                    {
                        "id": "boss_down",
                        "summary": "Defeat the gatekeeper",
                        "completes_on": {
                            "event": "combat.encounter_ended",
                            "encounter_id": "boss",
                        },
                    },
                ],
            }
        ]
    }))
    (chap_b / "summary.md").write_text("The road south.")
    return ChapterService(base=base)


def test_chapter_loader(tmp_path: Path):
    cs = _build_chapter_service(tmp_path)
    chap = cs.get("chapter_01")
    assert chap is not None
    assert chap.scenes[0].name == "Arrival"
    assert chap.scenes[0].beats[0].id == "meet_keeper"


def test_planner_summary_for_prompt(tmp_path: Path):
    cs = _build_chapter_service(tmp_path)
    p = StoryPlanner(chapters=cs)
    out = p.summary_for_prompt()
    assert "chapter_01" in out.lower() or "candlekeep" in out.lower()
    assert "Arrival" in out
    assert "Meet the keeper" in out


def test_planner_advances_chapter_in_order(tmp_path: Path):
    cs = _build_chapter_service(tmp_path)
    p = StoryPlanner(chapters=cs)
    assert p.state.current_chapter == "chapter_01"
    p.advance_chapter(reason="testing")
    assert p.state.current_chapter == "chapter_02"


def test_completes_on_encounter_marks_beat_done(tmp_path: Path):
    cs = _build_chapter_service(tmp_path)
    bus = EventBus()
    p = StoryPlanner(chapters=cs, event_bus=bus)
    bus.publish("combat.encounter_ended", {"encounter_id": "boss"})
    assert "boss_down" in p.state.beats_completed


def test_threads_open_and_resolve(tmp_path: Path):
    cs = _build_chapter_service(tmp_path)
    p = StoryPlanner(chapters=cs)
    p.open_thread("rumour", "find the missing scribe")
    assert p.state.threads[0].status == "open"
    p.resolve_thread("rumour")
    assert p.state.threads[0].status == "resolved"


def test_snapshot_round_trip(tmp_path: Path):
    cs = _build_chapter_service(tmp_path)
    p = StoryPlanner(chapters=cs)
    p.advance("meet_keeper", reason="met")
    snap = p.snapshot()

    p2 = StoryPlanner(chapters=cs)
    p2.restore(snap)
    assert "meet_keeper" in p2.state.beats_completed


def test_intent_resolved_logged_to_timeline(tmp_path: Path):
    cs = _build_chapter_service(tmp_path)
    bus = EventBus()
    p = StoryPlanner(chapters=cs, event_bus=bus)
    bus.publish("intent.resolved", {"intent": {"type": "speak", "raw_text": "hi"}})
    assert any(e.kind == "intent" for e in p.timeline.all())

