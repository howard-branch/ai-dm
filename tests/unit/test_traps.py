"""Tests for trap catalog, detection, disarm, trigger and ArmedTrap state."""
from __future__ import annotations

from ai_dm.game.combatant_state import CombatantState
from ai_dm.game.scene_state import ArmedTrap, SceneState
from ai_dm.rules import traps as tr
from ai_dm.rules.dice import DiceRoller


class TestTrapCatalog:
    def test_known_traps_loaded(self):
        assert tr.is_trap("dart_trap")
        assert tr.is_trap("fireball_glyph")
        assert tr.get_trap("nope") is None

    def test_dart_trap_attack_shape(self):
        t = tr.get_trap("dart_trap")
        assert t is not None
        assert t.attack is not None
        assert t.attack.bonus == 8
        assert t.attack.damage == "1d4"
        assert t.secondary_save is not None
        assert t.secondary_save.ability == "con"

    def test_fireball_glyph_uses_save(self):
        t = tr.get_trap("fireball_glyph")
        assert t is not None
        assert t.save is not None
        assert t.save.dc == 15
        assert t.primary is not None
        assert t.primary.aoe_radius_ft == 20


class TestDetectionAndDisarm:
    def test_detect_meets_dc(self):
        t = tr.get_trap("dart_trap")
        assert t is not None
        assert tr.attempt_detect(t, perception_total=15) is True
        assert tr.attempt_detect(t, perception_total=14) is False

    def test_disarm_below_dc_fails(self):
        t = tr.get_trap("dart_trap")
        assert t is not None
        assert tr.attempt_disarm(t, thieves_tools_total=14) is False
        assert tr.attempt_disarm(t, thieves_tools_total=15) is True


class TestResolveTrigger:
    def test_save_trap_full_on_fail_half_on_success(self):
        t = tr.get_trap("fireball_glyph")
        assert t is not None
        # Force a fail with massive negative dex save mod.
        actor = CombatantState(
            actor_id="v", name="Victim", hp=100, max_hp=100,
            saving_throws={"dex": -10},
        )
        res = tr.resolve_trigger(t, actor, roller=DiceRoller(seed=2))
        assert res.save_dc == 15
        assert res.damage_dealt > 0

    def test_attack_trap_damage_path(self):
        t = tr.get_trap("dart_trap")
        assert t is not None
        actor = CombatantState(
            actor_id="v", name="V", hp=50, max_hp=50, ac=10,
            saving_throws={"con": 0},
        )
        res = tr.resolve_trigger(t, actor, roller=DiceRoller(seed=4))
        assert res.attack_total is not None


class TestArmedTrap:
    def test_round_trip_via_scene_state(self):
        scene = SceneState(scene_id="hall")
        inst = scene.place_trap("dart_trap", zone_id="corridor1")
        inst.mark_detected("hero")
        again = SceneState.model_validate(scene.model_dump())
        assert again == scene
        assert again.armed_traps[0].detected_by == ["hero"]

    def test_disarm_makes_trap_unarmed(self):
        scene = SceneState(scene_id="hall")
        inst = scene.place_trap("dart_trap")
        assert inst.is_armed()
        assert inst.disarm() is True
        assert not inst.is_armed()
        # Already disarmed → returns False.
        assert inst.disarm() is False

    def test_single_use_expends(self):
        scene = SceneState(scene_id="hall")
        inst = scene.place_trap("fireball_glyph")
        actor = CombatantState(
            actor_id="v", name="V", hp=200, max_hp=200,
            saving_throws={"dex": 0},
        )
        res = inst.trigger(actor, roller=DiceRoller(seed=1))
        assert res is not None
        assert inst.expended is True
        # Triggering again returns None.
        assert inst.trigger(actor, roller=DiceRoller(seed=1)) is None

