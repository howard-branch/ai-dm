"""Tests for monster / NPC stat-block modeling and lifecycle state."""
from __future__ import annotations

import pytest

from ai_dm.game.combatant_state import (
    CombatantState,
    LegendaryAction,
    MonsterAction,
    MonsterReaction,
    Senses,
    Trait,
)
from ai_dm.game.monster_state import (
    MonsterAttack,
    StatBlock,
    cr_to_xp,
)
from ai_dm.game.npc_manager import NPCManager


# --------------------------------------------------------------------- #
# CR → XP table
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "cr, xp",
    [
        (0, 10), (0.125, 25), (0.25, 50), (0.5, 100),
        (1, 200), (5, 1800), (17, 18000), (30, 155000),
        ("1/4", 50), ("1/2", 100), ("3", 700),
    ],
)
def test_cr_to_xp_table(cr, xp):
    assert cr_to_xp(cr) == xp


def test_cr_to_xp_unknown_returns_none():
    assert cr_to_xp(None) is None
    assert cr_to_xp("garbage") is None
    assert cr_to_xp(99) is None


# --------------------------------------------------------------------- #
# StatBlock construction + extras-forbid
# --------------------------------------------------------------------- #


def _ancient_dragon() -> StatBlock:
    return StatBlock(
        key="ancient_red_dragon",
        name="Ancient Red Dragon",
        size="Gargantuan", type="dragon",
        ac=22, hp=546, speed=40,
        speeds={"walk": 40, "fly": 80, "climb": 40},
        abilities={"str": 30, "dex": 10, "con": 29, "int": 18, "wis": 15, "cha": 23},
        saving_throws={"dex": 7, "con": 16, "wis": 9, "cha": 13},
        skills={"perception": 16, "stealth": 7},
        proficiency_bonus=7,
        immunities=["fire"],
        condition_immunities=["charmed", "frightened", "paralyzed"],
        senses=Senses(blindsight=60, darkvision=120, passive_perception=26),
        languages=["Common", "Draconic"],
        challenge_rating=24,
        traits=[
            Trait(key="legendary_resistance", name="Legendary Resistance (4/Day)",
                  description="If the dragon fails a save, it can choose to succeed."),
        ],
        actions=[
            MonsterAction(
                key="bite", name="Bite",
                attack=MonsterAttack(
                    kind="melee", to_hit=17, reach=15,
                    damage="2d10+10", damage_type="piercing",
                    extra_damage="4d6", extra_damage_type="fire",
                ),
            ),
            MonsterAction(
                key="fire_breath", name="Fire Breath",
                usage="recharge", recharge="5-6",
                attack=MonsterAttack(
                    kind="spell", save_ability="dex", save_dc=24,
                    damage="26d6", damage_type="fire", half_on_save=True,
                ),
            ),
        ],
        reactions=[
            MonsterReaction(
                key="tail_attack", name="Tail Attack",
                trigger="A creature the dragon can see ends its turn within 20 feet.",
            ),
        ],
        legendary_actions=[
            LegendaryAction(key="detect", name="Detect", cost=1),
            LegendaryAction(key="tail_attack_la", name="Tail Attack", cost=1,
                            attack=MonsterAttack(kind="melee", to_hit=17, reach=20,
                                                 damage="2d8+10", damage_type="bludgeoning")),
            LegendaryAction(key="wing_attack", name="Wing Attack", cost=2),
        ],
        legendary_actions_per_round=3,
    )


def test_stat_block_round_trips_and_forbids_extras():
    sb = _ancient_dragon()
    parsed = StatBlock.model_validate(sb.model_dump())
    assert parsed == sb
    with pytest.raises(Exception):
        StatBlock.model_validate({**sb.model_dump(), "junk": True})


def test_derived_xp_uses_table_when_explicit_xp_missing():
    sb = _ancient_dragon()
    assert sb.xp is None
    assert sb.derived_xp() == 62000  # CR 24
    sb2 = sb.model_copy(update={"xp": 99999})
    assert sb2.derived_xp() == 99999


# --------------------------------------------------------------------- #
# Hydration: StatBlock → CombatantState
# --------------------------------------------------------------------- #


def test_to_combatant_copies_full_stat_block_surface():
    sb = _ancient_dragon()
    c = sb.to_combatant(actor_id="dragon_001", token_id="tok_d")
    assert c.actor_id == "dragon_001"
    assert c.token_id == "tok_d"
    assert c.source == "npc"
    assert c.team == "foe"
    assert c.controller == "ai"
    assert c.hp == c.max_hp == 546
    assert c.ac == 22
    assert c.speed == 40
    assert "fire" in c.immunities
    assert c.condition_immunities == ["charmed", "frightened", "paralyzed"]
    assert c.senses and c.senses.blindsight == 60
    assert "Draconic" in c.languages
    assert c.challenge_rating == 24
    assert c.xp == 62000  # auto from CR
    assert [t.key for t in c.traits] == ["legendary_resistance"]
    assert {a.key for a in c.actions} == {"bite", "fire_breath"}
    assert [r.key for r in c.reactions] == ["tail_attack"]
    assert {la.key for la in c.legendary_actions} == {"detect", "tail_attack_la", "wing_attack"}
    assert c.legendary_actions_per_round == 3
    assert c.legendary_actions_remaining == 3
    assert c.stat_block_key == "ancient_red_dragon"


def test_combatant_default_pc_has_no_monster_surface():
    c = CombatantState(actor_id="hero", name="Hero")
    assert c.condition_immunities == []
    assert c.senses is None
    assert c.languages == []
    assert c.challenge_rating is None
    assert c.xp is None
    assert c.traits == []
    assert c.actions == []
    assert c.reactions == []
    assert c.legendary_actions == []
    assert c.legendary_actions_per_round is None
    assert c.legendary_actions_remaining is None
    assert c.stat_block_key is None


# --------------------------------------------------------------------- #
# Runtime mutators
# --------------------------------------------------------------------- #


def test_spend_legendary_decrements_until_empty():
    c = _ancient_dragon().to_combatant(actor_id="d")
    assert c.spend_legendary(2) is True
    assert c.legendary_actions_remaining == 1
    assert c.spend_legendary(2) is False  # not enough
    assert c.legendary_actions_remaining == 1
    assert c.spend_legendary(1) is True
    assert c.spend_legendary(1) is False  # exhausted


def test_spend_legendary_returns_false_for_non_legendary():
    c = CombatantState(actor_id="goblin", name="Goblin")
    assert c.spend_legendary(1) is False


def test_start_of_round_refills_legendary_budget():
    c = _ancient_dragon().to_combatant(actor_id="d")
    c.spend_legendary(3)
    assert c.legendary_actions_remaining == 0
    c.start_of_round()
    assert c.legendary_actions_remaining == 3


def test_has_condition_immunity_is_case_insensitive():
    c = _ancient_dragon().to_combatant(actor_id="d")
    assert c.has_condition_immunity("Charmed") is True
    assert c.has_condition_immunity("paralyzed") is True
    assert c.has_condition_immunity("prone") is False


# --------------------------------------------------------------------- #
# from_npc_block accepts the rich monster shape
# --------------------------------------------------------------------- #


def test_from_npc_block_accepts_full_monster_payload():
    block = {
        "id": "ogre_1", "name": "Ogre", "hp": 59, "max_hp": 59,
        "ac": 11, "speed": 40,
        "condition_immunities": ["frightened"],
        "senses": {"darkvision": 60, "passive_perception": 8},
        "languages": ["Common", "Giant"],
        "challenge_rating": 2,
        "traits": [{"key": "aggressive", "name": "Aggressive"}],
        "actions": [
            {"key": "greatclub", "name": "Greatclub",
             "attack": {"kind": "melee", "to_hit": 6, "reach": 5,
                         "damage": "2d8+4", "damage_type": "bludgeoning"}},
        ],
        "reactions": [],
        "legendary_actions": [],
    }
    c = CombatantState.from_npc_block(block)
    assert c.condition_immunities == ["frightened"]
    assert c.senses and c.senses.darkvision == 60
    assert c.languages == ["Common", "Giant"]
    assert c.challenge_rating == 2.0
    assert c.xp == 450  # auto from CR
    assert [t.key for t in c.traits] == ["aggressive"]
    assert c.actions[0].attack and c.actions[0].attack.damage == "2d8+4"


# --------------------------------------------------------------------- #
# NPCManager
# --------------------------------------------------------------------- #


def test_npc_manager_register_and_spawn():
    mgr = NPCManager()
    sb = _ancient_dragon()
    mgr.register_stat_block(sb)
    assert mgr.get_stat_block("ancient_red_dragon") is sb

    npc = mgr.spawn("ancient_red_dragon", actor_id="d1", token_id="t1")
    assert isinstance(npc, CombatantState)
    assert mgr.get_npc("d1") is npc
    assert mgr.spawned() == [npc]


def test_npc_manager_rejects_unknown_and_duplicate_spawns():
    mgr = NPCManager()
    mgr.register_stat_block(_ancient_dragon())
    mgr.spawn("ancient_red_dragon", actor_id="d1")
    with pytest.raises(KeyError):
        mgr.spawn("nonexistent", actor_id="d2")
    with pytest.raises(ValueError):
        mgr.spawn("ancient_red_dragon", actor_id="d1")


def test_npc_manager_despawn_removes_instance():
    mgr = NPCManager()
    mgr.register_stat_block(_ancient_dragon())
    mgr.spawn("ancient_red_dragon", actor_id="d1")
    gone = mgr.despawn("d1")
    assert gone is not None and gone.actor_id == "d1"
    assert mgr.get_npc("d1") is None
    assert mgr.despawn("d1") is None  # idempotent


def test_npc_manager_snapshot_round_trip():
    mgr = NPCManager()
    mgr.register_stat_block(_ancient_dragon())
    mgr.spawn("ancient_red_dragon", actor_id="d1")
    blob = mgr.snapshot()

    mgr2 = NPCManager()
    mgr2.restore(blob)
    assert mgr2.get_stat_block("ancient_red_dragon") is not None
    npc = mgr2.get_npc("d1")
    assert npc is not None
    assert npc.legendary_actions_per_round == 3
    assert [a.key for a in npc.actions] == ["bite", "fire_breath"]

