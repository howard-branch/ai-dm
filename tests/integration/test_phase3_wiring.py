"""End-to-end Phase-3 wiring through the Container."""
from __future__ import annotations

from pathlib import Path

from ai_dm.ai.intent_schemas import PlayerIntent
from ai_dm.app.container import Container, ContainerConfig
from ai_dm.campaign.pack import CampaignPack
from ai_dm.game.combat_state import Participant
from ai_dm.memory.npc_memory import MemoryEvent
from ai_dm.rules.engine import ActorRuleState


def _build(tmp: Path) -> Container:
    pack = CampaignPack.load(
        Path("campaigns/morgana"),
        state_root=tmp / "campaigns",
    )
    cfg = ContainerConfig(
        pack=pack,
        rules_assets=Path("assets/rules"),
        max_backups=3,
        audio_enabled=False,        # don't spawn background TTS thread
        triggers_enabled=True,
        inbound_foundry_enabled=False,  # don't touch the websocket client
    )
    return Container.build(cfg)


def test_container_wires_phase3_subsystems(tmp_path: Path):
    c = _build(tmp_path)
    try:
        # Rules engine is reachable.
        assert c.rules is not None
        assert c.action_resolver is not None

        # Intent parser fast-path works.
        intent = c.intent_parser.parse("I attack the goblin")
        assert intent.type == "attack"
        assert intent.target_id == "goblin"

        # Story planner has loaded the campaign chapters.
        assert c.story_planner is not None
        hint = c.story_planner.summary_for_prompt()
        assert isinstance(hint, str)

        # Triggers loaded from assets/campaign/triggers (best-effort).
        assert c.triggers is not None

        # Audio queue exists and is idle.
        assert c.audio_queue is not None
        assert c.audio_queue.pending() == 0
    finally:
        c.shutdown()


def test_attack_intent_through_action_resolver(tmp_path: Path):
    c = _build(tmp_path)
    try:
        target = ActorRuleState(actor_id="goblin", name="Goblin", hp=10, max_hp=10, ac=8)
        c.action_resolver.actor_lookup = lambda aid: target if aid == "goblin" else None

        intent = PlayerIntent(
            type="attack", actor_id="hero", target_id="goblin", raw_text="attack"
        )
        env = c.intent_router.handle(intent, ctx={"attack_modifier": 10, "damage_dice": "1d4"})
        assert env.resolution is not None
        assert env.resolution.type == "attack"
    finally:
        c.shutdown()


def test_phase3_save_load_round_trip(tmp_path: Path):
    c1 = _build(tmp_path)
    try:
        c1.registry.register("scene", "s-x", name="sanctum")
        c1.npc_memory.record("g", MemoryEvent(text="cast a spell"))
        c1.relationships.set("g", "morgana", -25, tags=["betrayed"])
        c1.combat.start_encounter("e1", [Participant(actor_id="g", name="G")])
        c1.combat.roll_initiative()
        c1.flags["noticed_door"] = True

        # Phase 3 state we want to round-trip.
        c1.story_planner.advance_chapter("chapter_02", reason="testing")
        c1.story_planner.open_thread("rumour", "find the scribe")
        c1.timeline.record("note", "the door creaked")

        # Trigger that has fired once.
        from ai_dm.orchestration.triggers import Trigger

        c1.triggers.register(Trigger(id="t-once", event="x", do=[lambda p, c: None], once=True))
        c1.event_bus.publish("x", {})

        c1.actor_state["hero"] = {"hp": 7, "name": "Hero"}
        c1.foundry_journals["recap"] = "j-1"

        c1.campaign_store.save()
    finally:
        c1.shutdown()

    c2 = _build(tmp_path)
    try:
        c2.campaign_store.restore_into_runtime()
        assert c2.registry.resolve("scene", "sanctum") == "s-x"
        assert c2.flags["noticed_door"] is True
        assert c2.story_planner.state.current_chapter == "chapter_02"
        assert any(t.id == "rumour" for t in c2.story_planner.state.threads)
        assert any(e.summary == "the door creaked" for e in c2.timeline.all())
        assert "t-once" in c2.triggers.fired_once_ids
        assert c2.actor_state["hero"]["hp"] == 7
        assert c2.foundry_journals["recap"] == "j-1"
    finally:
        c2.shutdown()


def test_v2_save_migrates_to_v3(tmp_path: Path):
    """Older v2 saves load cleanly with empty Phase-3 sections."""
    import json

    save_dir = tmp_path / "campaigns" / "morgana" / "saves"
    save_dir.mkdir(parents=True)
    (save_dir / "campaign_state.json").write_text(json.dumps({
        "schema_version": 2,
        "registry": {},
        "locations": {},
        "npc_memory": {},
        "relationships": [],
        "combat": None,
        "flags": {"legacy": True},
        "session": {},
    }))

    c = _build(tmp_path)
    try:
        c.campaign_store.restore_into_runtime()
        assert c.flags["legacy"] is True
        assert c.story_planner.state.beats_completed == []
    finally:
        c.shutdown()

