"""``EncounterManager`` should spawn NPCs into Foundry on start."""
from __future__ import annotations

from typing import Any

from ai_dm.game.encounter_manager import EncounterManager
from ai_dm.models.commands import (
    CreateActorCommand,
    SpawnTokenCommand,
)


class _StubCombat:
    def __init__(self) -> None:
        self.state = None

    def start_encounter(self, encounter_id, participants):
        # Just record without doing real combat-state work.
        class _S:
            phase = "idle"
        self.state = _S()
        self.state.encounter_id = encounter_id
        self.state.participants = participants


class _StubChapters:
    def __init__(self, encounters: list[dict]) -> None:
        self._encs = encounters

    def all(self) -> list:
        class _Chap:
            pass
        c = _Chap()
        c.encounters = self._encs
        return [c]


class _StubExecutor:
    def __init__(self) -> None:
        self.executed: list = []
        # ``EncounterManager`` reads ``executor.registry`` to dedupe.
        self.registry = None

    def execute(self, validated, *, atomic=False, **_):
        self.executed.append((list(validated), atomic))

        class _R:
            ok = True
            error = None
            results: list = []
        return _R()


class _StubValidator:
    def validate(self, cmd):
        return cmd  # passthrough


class _StubRouter:
    def __init__(self) -> None:
        self.executor = _StubExecutor()
        self.validator = _StubValidator()
        self.location_service = None


def test_start_encounter_spawns_npcs_into_foundry():
    enc = {
        "id": "encounter.grukk_alone",
        "scene_id": "stone_chamber",
        "monsters": [
            {"key": "mon.grukk", "name": "Grukk", "hp": 30, "ac": 14},
            {"key": "mon.goblin", "name": "Goblin", "count": 2,
             "hp": 7, "ac": 13},
        ],
    }
    combat = _StubCombat()
    router = _StubRouter()
    em = EncounterManager(
        combat=combat,
        chapters=_StubChapters([enc]),
        event_bus=None,
        pack=None,                   # _build_party_participants -> []
        turn_manager=None,
        story_planner=None,
        command_router=router,
    )
    # ``pack`` guards the spawn helper too — give it a dummy truthy
    # object so we exercise the full path without dragging in
    # CampaignPack / bootstrap helpers.
    em.pack = object()

    assert em.start_encounter("encounter.grukk_alone") is True

    # One executor.execute call with create+spawn for each NPC.
    assert len(router.executor.executed) == 1
    cmds, atomic = router.executor.executed[0]
    assert atomic is False
    creates = [c for c in cmds if isinstance(c, CreateActorCommand)]
    spawns = [c for c in cmds if isinstance(c, SpawnTokenCommand)]
    # 1 grukk + 2 goblins = 3 NPC participants.
    assert len(creates) == 3
    assert len(spawns) == 3
    # Aliases include the canonical participant id so apply_damage
    # later resolves through the registry.
    grukk = next(c for c in creates if c.name == "Grukk")
    assert "mon.grukk" in (grukk.aliases or [])
    assert grukk.actor_type == "npc"
    assert grukk.system["attributes"]["hp"]["max"] == 30
    assert grukk.system["attributes"]["ac"]["value"] == 14
    # Tokens are placed on the encounter's authored scene.
    assert {s.scene_id for s in spawns} == {"stone_chamber"}

