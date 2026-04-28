"""Tests for light/vision/obscurement and per-scene LightSourceInstance state."""
from __future__ import annotations

from ai_dm.game.scene_state import LightSourceInstance, SceneState
from ai_dm.rules import light as lt


class TestLightCatalog:
    def test_torch_radii(self):
        t = lt.get_light("torch")
        assert t is not None
        assert t.bright_ft == 20
        assert t.dim_ft == 40
        assert t.duration_min == 60

    def test_lantern_duration_6h(self):
        l = lt.get_light("lantern_hooded")
        assert l is not None
        assert l.duration_min == 360


class TestVisionAt:
    def test_within_bright_radius(self):
        torch = lt.get_light("torch")
        assert torch is not None
        assert lt.vision_at(10, torch) == "bright"
        assert lt.vision_at(20, torch) == "bright"

    def test_dim_band(self):
        torch = lt.get_light("torch")
        assert torch is not None
        assert lt.vision_at(30, torch) == "dim"
        assert lt.vision_at(40, torch) == "dim"

    def test_beyond_dim_is_darkness(self):
        torch = lt.get_light("torch")
        assert torch is not None
        assert lt.vision_at(100, torch) == "darkness"


class TestEffectiveVision:
    def test_overlapping_torches_pick_brightest(self):
        torch = lt.get_light("torch")
        candle = lt.get_light("candle")
        assert torch and candle
        # Inside the torch's bright radius even though candle would be dim.
        v = lt.effective_vision([(15, torch), (5, candle)])
        assert v == "bright"

    def test_darkvision_promotes_dim_to_bright(self):
        torch = lt.get_light("torch")
        assert torch is not None
        # 30 ft → dim from torch.
        v = lt.effective_vision([(30, torch)], has_darkvision=True)
        assert v == "bright"


class TestAttackAndPerception:
    def test_heavy_obscurement_blocks_attacker(self):
        assert lt.attack_modifier("bright", "heavily") == "blocked"

    def test_attacker_in_darkness_without_darkvision_blocked(self):
        assert lt.attack_modifier("darkness", "clear") == "blocked"

    def test_attacker_in_dim_has_disadvantage(self):
        assert lt.attack_modifier("dim", "clear") == "disadvantage"

    def test_lightly_obscured_perception_disadvantage(self):
        assert lt.perception_modifier("lightly") == "disadvantage"

    def test_heavily_obscured_perception_auto_fail(self):
        assert lt.perception_modifier("heavily") == "auto_fail"


class TestLightSourceInstance:
    def test_tick_burns_minutes(self):
        scene = SceneState(scene_id="cave")
        src = scene.add_light("torch")
        assert src.minutes_remaining == 60
        burned = src.tick(30)
        assert burned is False
        assert src.minutes_remaining == 30

    def test_torch_burns_out_after_60(self):
        scene = SceneState(scene_id="cave")
        src = scene.add_light("torch")
        assert src.tick(60) is True
        assert src.lit is False

    def test_extinguish_and_relight(self):
        scene = SceneState(scene_id="cave")
        src = scene.add_light("torch")
        assert src.extinguish() is True
        assert src.lit is False
        assert src.light() is True
        assert src.lit is True

    def test_round_trip(self):
        scene = SceneState(scene_id="cave")
        scene.add_light("torch", carrier_actor_id="hero")
        scene.add_light("lantern_hooded", anchor_id="table")
        again = SceneState.model_validate(scene.model_dump())
        assert again == scene
        assert isinstance(again.light_sources[0], LightSourceInstance)

