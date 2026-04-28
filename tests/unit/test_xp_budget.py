"""Tests for the XP-budget catalog, encounter classification, and PartyState."""
from __future__ import annotations

from ai_dm.game.party_state import PartyState
from ai_dm.rules import xp_budget as xb


class TestThresholds:
    def test_level_1_thresholds(self):
        t = xb.thresholds_for_level(1)
        assert t.easy == 25 and t.medium == 50 and t.hard == 75 and t.deadly == 100

    def test_party_thresholds_sum_per_pc(self):
        # Four level-1 PCs.
        thr = xb.party_thresholds([1, 1, 1, 1])
        assert thr["easy"] == 100
        assert thr["medium"] == 200
        assert thr["hard"] == 300
        assert thr["deadly"] == 400


class TestGroupMultiplier:
    def test_solo_is_1x(self):
        assert xb.group_multiplier(1) == 1.0

    def test_pair_is_1_5x(self):
        assert xb.group_multiplier(2) == 1.5

    def test_three_to_six_is_2x(self):
        assert xb.group_multiplier(3) == 2.0
        assert xb.group_multiplier(6) == 2.0

    def test_15_or_more_is_4x(self):
        assert xb.group_multiplier(20) == 4.0


class TestAdjustedXp:
    def test_three_goblins_apply_2x(self):
        # Goblin = 50 XP × 3 = 150 base × 2.0 (3-6 monsters) = 300, but
        # a 4-PC party is the "average" so multiplier stays 2x.
        adj = xb.adjusted_xp([50, 50, 50], party_size=4)
        assert adj == 300

    def test_small_party_bumps_multiplier_up(self):
        # 1 monster, 2-PC party: multiplier moves from 1.0 → 1.5.
        assert xb.adjusted_xp([100], party_size=2) == 150

    def test_large_party_bumps_multiplier_down(self):
        # 1 monster, 6-PC party: 1.0 → no negative slot; stays 1.0.
        assert xb.adjusted_xp([100], party_size=6) == 100


class TestClassifyEncounter:
    def test_easy_against_low_party(self):
        # Single 50-xp goblin vs 4 lv1 PCs → adjusted 50, easy.
        assert xb.classify_encounter([50], [1, 1, 1, 1]) == "easy"

    def test_deadly_against_low_party(self):
        # 4 ogres (450 each) vs 4 lv1 PCs.
        assert xb.classify_encounter([450, 450, 450, 450], [1, 1, 1, 1]) == "deadly"


class TestLevelXp:
    def test_xp_for_level(self):
        assert xb.xp_for_level(1) == 0
        assert xb.xp_for_level(2) == 300
        assert xb.xp_for_level(20) == 355000

    def test_level_for_xp(self):
        assert xb.level_for_xp(299) == 1
        assert xb.level_for_xp(300) == 2
        assert xb.level_for_xp(355_000) == 20

    def test_xp_to_next_level(self):
        assert xb.xp_to_next_level(0, 1) == 300
        assert xb.xp_to_next_level(450, 2) == 450  # 900 - 450


class TestAwardXp:
    def test_split_evenly(self):
        out = xb.award_xp(["a", "b", "c", "d"], 400)
        assert out == {"a": 100, "b": 100, "c": 100, "d": 100}

    def test_remainder_to_first(self):
        out = xb.award_xp(["a", "b", "c"], 100)
        assert out == {"a": 34, "b": 33, "c": 33}

    def test_zero_xp_yields_zeros(self):
        assert xb.award_xp(["a"], 0) == {"a": 0}


class TestPartyState:
    def test_record_kill_then_finalize_distributes(self):
        ps = PartyState()
        for m in ("a", "b"):
            ps.add_member(m, level=1, xp=0)
        ps.record_kill(monster_id="m1", cr=1.0, xp=200, encounter_id="e1")
        ps.record_kill(monster_id="m2", cr=1.0, xp=200, encounter_id="e1")
        deltas = ps.finalize_encounter("e1")
        assert deltas == {"a": 200, "b": 200}
        assert ps.xp_pool == {"a": 200, "b": 200}
        assert ps.pending_xp == 0

    def test_level_up_triggers_when_threshold_crossed(self):
        ps = PartyState()
        ps.add_member("a", level=1, xp=0)
        ps.record_kill(monster_id=None, cr=None, xp=300, encounter_id="e1")
        ps.finalize_encounter("e1")
        assert ps.levels["a"] == 2

    def test_round_trip(self):
        ps = PartyState(members=["a"], xp_pool={"a": 50}, levels={"a": 1})
        ps.record_kill(monster_id="m", cr=0.5, xp=100, encounter_id="e1")
        again = PartyState.model_validate(ps.model_dump())
        assert again == ps

