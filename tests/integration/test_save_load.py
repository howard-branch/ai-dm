"""Save/load smoke through the Phase-2 container."""
from __future__ import annotations

from pathlib import Path

from ai_dm.app.container import Container, ContainerConfig
from ai_dm.campaign.pack import CampaignPack
from ai_dm.game.combat_state import Participant
from ai_dm.memory.npc_memory import MemoryEvent


def _build(tmp: Path) -> Container:
    pack = CampaignPack.load(
        Path("campaigns/morgana"),
        state_root=tmp / "campaigns",
    )
    cfg = ContainerConfig(pack=pack, max_backups=3)
    return Container.build(cfg)


def test_save_then_load_round_trip(tmp_path: Path):
    c1 = _build(tmp_path)
    try:
        c1.registry.register("scene", "s-x", name="sanctum")
        c1.npc_memory.record("g", MemoryEvent(text="cast a spell"))
        c1.relationships.set("g", "morgana", -25, tags=["betrayed"])
        c1.combat.start_encounter("e1", [Participant(actor_id="g", name="G")])
        c1.combat.roll_initiative()
        c1.campaign_store.flags["noticed_door"] = True
        c1.campaign_store.save()
    finally:
        c1.shutdown()

    c2 = _build(tmp_path)
    try:
        c2.campaign_store.restore_into_runtime()
        assert c2.registry.resolve("scene", "sanctum") == "s-x"
        assert c2.npc_memory.recent("g")[0].text == "cast a spell"
        assert c2.relationships.get("g", "morgana").disposition == -25
        assert c2.combat.state is not None
        assert c2.combat.state.encounter_id == "e1"
        assert c2.campaign_store.flags["noticed_door"] is True
    finally:
        c2.shutdown()
