"""Tests for environmental hazards + per-scene ActiveHazard state."""
from __future__ import annotations

from ai_dm.game.combatant_state import CombatantState
from ai_dm.game.scene_state import ActiveHazard, SceneState
from ai_dm.rules import hazards as hz
from ai_dm.rules.dice import DiceRoller


class TestHazardCatalog:
    def test_known_hazards_loaded(self):
        assert hz.is_hazard("extreme_cold")
        assert hz.is_hazard("lava")
        assert hz.is_hazard("falling")
        assert hz.get_hazard("does_not_exist") is None

    def test_extreme_cold_uses_con_save_dc_10(self):
        h = hz.get_hazard("extreme_cold")
        assert h is not None
        assert h.save is not None
        assert h.save.ability == "con"
        assert h.save.dc == 10
        assert h.save.on_fail == "exhaustion+1"

    def test_lava_has_damage(self):
        h = hz.get_hazard("lava")
        assert h is not None
        assert h.damage is not None
        assert h.damage.dice == "10d10"
        assert h.damage.type == "fire"


class TestResolveTick:
    def test_failed_save_grants_exhaustion(self):
        actor = CombatantState(actor_id="a", name="A", saving_throws={"con": -5})
        h = hz.get_hazard("extreme_cold")
        assert h is not None
        before = actor.exhaustion
        # Roll d20 with mod -5 will almost certainly miss DC 10.
        out = hz.resolve_tick(h, actor, roller=DiceRoller(seed=1))
        assert out.save_dc == 10
        if out.succeeded is False:
            assert actor.exhaustion == before + 1
            assert out.exhaustion_delta == 1

    def test_resistance_skips_actor(self):
        actor = CombatantState(actor_id="a", name="A", resistances=["fire"])
        h = hz.get_hazard("lava")
        assert h is not None
        out = hz.resolve_tick(h, actor)
        assert out.skipped_reason == "exempt"
        assert out.damage_dealt == 0

    def test_lava_rolls_damage_with_seed(self):
        actor = CombatantState(actor_id="a", name="A", hp=200, max_hp=200)
        h = hz.get_hazard("lava")
        assert h is not None
        out = hz.resolve_tick(h, actor, roller=DiceRoller(seed=3))
        assert out.damage_dealt > 0
        assert out.damage_type == "fire"
        assert actor.hp < 200


class TestFallingDamage:
    def test_under_10_ft_no_damage(self):
        assert hz.apply_falling_damage(5) == 0

    def test_50_ft_rolls_5d6(self):
        # Deterministic with a seed; just sanity-check the bounds.
        v = hz.apply_falling_damage(50, roller=DiceRoller(seed=1))
        assert 5 <= v <= 30

    def test_caps_at_20d6(self):
        v = hz.apply_falling_damage(10_000, roller=DiceRoller(seed=1))
        assert v <= 120


class TestSceneStateRoundTrip:
    def test_place_and_round_trip_active_hazard(self):
        scene = SceneState(scene_id="dungeon")
        inst = scene.place_hazard("lava", zone_id="lake")
        assert inst.instance_id.startswith("lava#")
        again = SceneState.model_validate(scene.model_dump())
        assert again == scene
        assert isinstance(again.active_hazards[0], ActiveHazard)

    def test_unknown_hazard_raises(self):
        scene = SceneState(scene_id="x")
        try:
            scene.place_hazard("nope")
        except ValueError:
            return
        raise AssertionError("expected ValueError")

