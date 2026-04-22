import json
from pathlib import Path

from ai_dm.foundry.registry import FoundryRegistry
from ai_dm.game.combat_machine import CombatMachine
from ai_dm.game.combat_state import Participant
from ai_dm.game.location_model import Anchor, SceneLocation
from ai_dm.game.location_service import LocationService
from ai_dm.memory.npc_memory import MemoryEvent, NPCMemoryStore
from ai_dm.memory.relationships import RelationshipMatrix
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.persistence.campaign_store import CampaignStore


def _populate():
    reg = FoundryRegistry()
    reg.register("scene", "s1", name="hall")
    reg.register("actor", "a1", name="Goblin")
    reg.register("token", "t1", name="goblin", scene_id="s1")

    loc = LocationService(registry=reg)
    loc.load_scene(SceneLocation(
        scene_id="s1",
        anchors=[Anchor(id="a", name="altar", scene_id="s1", x=10, y=20)],
    ))

    mem = NPCMemoryStore()
    mem.record("a1", MemoryEvent(text="saw the party"))

    rels = RelationshipMatrix()
    rels.set("a1", "morgana", -10)

    bus = EventBus()
    combat = CombatMachine(event_bus=bus, command_router=None)
    combat.start_encounter("enc-1", [Participant(actor_id="a1", name="Goblin")])
    combat.roll_initiative()
    return reg, loc, mem, rels, combat


def test_campaign_store_round_trip(tmp_path: Path):
    reg, loc, mem, rels, combat = _populate()
    store = CampaignStore(
        base=tmp_path,
        registry=reg, location_service=loc,
        npc_memory=mem, relationships=rels, combat=combat,
    )
    store.flags["noticed"] = True
    path = store.save()
    assert path.exists()

    # Build a fresh runtime and load.
    reg2 = FoundryRegistry()
    loc2 = LocationService(registry=reg2)
    mem2 = NPCMemoryStore()
    rels2 = RelationshipMatrix()
    bus2 = EventBus()
    combat2 = CombatMachine(event_bus=bus2, command_router=None)

    store2 = CampaignStore(
        base=tmp_path,
        registry=reg2, location_service=loc2,
        npc_memory=mem2, relationships=rels2, combat=combat2,
    )
    store2.restore_into_runtime()

    assert reg2.resolve("scene", "hall") == "s1"
    assert loc2.resolve_anchor("s1", "altar") == (10, 20)
    assert mem2.recent("a1")[0].text == "saw the party"
    assert rels2.get("a1", "morgana").disposition == -10
    assert combat2.state is not None
    assert combat2.state.encounter_id == "enc-1"
    assert store2.flags["noticed"] is True


def test_campaign_store_migrates_v1(tmp_path: Path):
    legacy = {"flags": {"foo": "bar"}}
    (tmp_path / "campaign_state.json").write_text(json.dumps(legacy))
    reg = FoundryRegistry()
    loc = LocationService(registry=reg)
    mem = NPCMemoryStore()
    rels = RelationshipMatrix()
    bus = EventBus()
    combat = CombatMachine(event_bus=bus, command_router=None)
    store = CampaignStore(
        base=tmp_path,
        registry=reg, location_service=loc,
        npc_memory=mem, relationships=rels, combat=combat,
    )
    store.restore_into_runtime()
    assert store.flags["foo"] == "bar"


def test_campaign_store_writes_backup_before_save(tmp_path: Path):
    reg, loc, mem, rels, combat = _populate()
    store = CampaignStore(
        base=tmp_path,
        registry=reg, location_service=loc,
        npc_memory=mem, relationships=rels, combat=combat,
    )
    store.save()
    store.save()  # second save should rotate the first
    backups = list((tmp_path / "backups").glob("campaign_state.*.json"))
    assert backups

