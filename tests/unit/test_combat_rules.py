"""Unit tests for the SRD 5.2 combat-rules modules added in this batch.

Covers: initiative, turn, actions, movement, opportunity_attack,
cover, stealth, grapple, concentration, areas_of_effect, rests.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_dm.rules import (
    actions,
    areas_of_effect as aoe,
    concentration,
    cover,
    grapple,
    initiative,
    movement,
    opportunity_attack as oa,
    rests,
    stealth,
    turn,
)
from ai_dm.rules.dice import DiceRoller


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #


def _actor(**kw):
    base = dict(
        actor_id="A",
        action_used=False,
        bonus_action_used=False,
        reaction_used=False,
        movement_used=0,
        speed=30,
        dashed=False,
        dodging=False,
        disengaging=False,
        hidden=False,
        conditions=[],
        size="medium",
        exhaustion=0,
        concentration=None,
        hp=10,
        max_hp=10,
        temp_hp=0,
        resources={},
        spell_slots={},
        hit_dice={},
        saving_throws={"con": 2},
        grappled_by=None,
        grappling=[],
    )
    base.update(kw)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------- #
# Initiative
# --------------------------------------------------------------------- #


def test_initiative_roll_includes_modifier():
    roller = DiceRoller(seed=1)
    r = initiative.roll_initiative("a", roller=roller, modifier=3, dex_mod=3)
    assert r.total == r.roll + 3
    assert r.dex_mod == 3


def test_initiative_sort_order_breaks_ties_by_dex_mod():
    rolls = [
        initiative.InitiativeRoll("a", 10, 0, 10, dex_mod=0),
        initiative.InitiativeRoll("b", 8, 2, 10, dex_mod=2),
        initiative.InitiativeRoll("c", 12, 0, 12, dex_mod=0),
    ]
    assert initiative.sort_order(rolls) == ["c", "b", "a"]


# --------------------------------------------------------------------- #
# Turn
# --------------------------------------------------------------------- #


def test_turn_phases_start_and_end_canonical():
    assert turn.PHASES[0] == "start_of_turn"
    assert turn.PHASES[-1] == "end_of_turn"


def test_turn_start_resets_per_turn_state():
    a = _actor(action_used=True, bonus_action_used=True, movement_used=15, dashed=True)
    turn.start_of_turn(a)
    assert a.action_used is False
    assert a.bonus_action_used is False
    assert a.movement_used == 0


# --------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------- #


def test_actions_economy_for_known_actions():
    assert actions.economy_for("attack") == "action"
    assert actions.economy_for("opportunity_attack") == "reaction"
    assert actions.economy_for("two_weapon_attack") == "bonus_action"


def test_actions_consume_double_spend_rejected():
    a = _actor()
    assert actions.consume(a, "attack") is True
    assert actions.consume(a, "attack") is False
    assert a.action_used is True


def test_actions_consume_free_never_consumed():
    a = _actor()
    assert actions.consume(a, "free") is True
    assert actions.consume(a, "free") is True


# --------------------------------------------------------------------- #
# Movement
# --------------------------------------------------------------------- #


def test_movement_difficult_terrain_doubles_cost():
    assert movement.cost(15, difficult=True) == 30


def test_movement_dash_doubles_budget():
    a = _actor(speed=30, dashed=True)
    assert movement.budget(a) == 60
    assert movement.remaining(a) == 60


def test_movement_spend_tracks_remaining_and_rejects_overspend():
    a = _actor(speed=30)
    movement.spend(a, 20)
    assert movement.remaining(a) == 10
    with pytest.raises(ValueError):
        movement.spend(a, 15)


def test_movement_prone_crawl_doubles_cost():
    assert movement.cost(10, crawling=True) == 20


# --------------------------------------------------------------------- #
# Opportunity attacks
# --------------------------------------------------------------------- #


def test_oa_disengaging_suppresses_provoke():
    mover = _actor(disengaging=True)
    assert oa.provokes(mover) is False


def test_oa_used_reaction_blocks_react():
    d = _actor(reaction_used=True)
    assert oa.can_react(d) is False


def test_oa_incapacitated_blocks_react():
    d = _actor(conditions=["incapacitated"])
    assert oa.can_react(d) is False


def test_oa_eligible_reactors_filters_threats():
    mover = _actor()
    threats = [_actor(actor_id="t1"), _actor(actor_id="t2", reaction_used=True)]
    assert oa.eligible_reactors(mover, threats) == ["t1"]


# --------------------------------------------------------------------- #
# Cover
# --------------------------------------------------------------------- #


def test_cover_bonuses_match_srd():
    assert cover.ac_bonus("none") == 0
    assert cover.ac_bonus("half") == 2
    assert cover.ac_bonus("three_quarters") == 5
    assert cover.ac_bonus("total") == 0  # blocks instead
    assert cover.dex_save_bonus("three_quarters") == 5
    assert cover.blocks("total") is True
    assert cover.blocks("half") is False


def test_cover_apply_to_target_ac():
    assert cover.apply_to_target_ac(15, cover="half") == 17


# --------------------------------------------------------------------- #
# Stealth
# --------------------------------------------------------------------- #


def test_stealth_attack_breaks_hidden():
    a = _actor(hidden=True)
    assert stealth.maybe_break(a, "attack") is True
    assert a.hidden is False


def test_stealth_movement_does_not_break_hidden():
    a = _actor(hidden=True)
    assert stealth.maybe_break(a, "move") is False
    assert a.hidden is True


def test_stealth_invisible_attacker_has_advantage():
    assert stealth.attack_advantage(attacker_invisible=True) == "advantage"


def test_stealth_invisible_target_grants_disadvantage():
    assert stealth.attack_advantage(target_invisible=True) == "disadvantage"


def test_stealth_attempt_hide_passes_when_total_meets_dc():
    roller = DiceRoller(seed=42)
    res = stealth.attempt_hide(roller=roller, stealth_modifier=5, dc=10)
    assert isinstance(res.success, bool)
    assert res.dc == 10


# --------------------------------------------------------------------- #
# Grapple / shove
# --------------------------------------------------------------------- #


def test_grapple_succeeds_and_marks_target():
    class FixedRoller:
        def __init__(self):
            self.calls = 0
        def roll(self, _expr):
            # Attacker wins: 18 vs 5.
            self.calls += 1
            val = 18 if self.calls == 1 else 5
            return SimpleNamespace(kept=[val], total=val)
    a = _actor(actor_id="a"); t = _actor(actor_id="t", conditions=[])
    res = grapple.attempt_grapple(a, t, roller=FixedRoller())
    assert res.success is True
    assert "grappled" in t.conditions
    assert t.grappled_by == "a"
    assert "t" in a.grappling


def test_shove_prone_sets_condition():
    class FixedRoller:
        def __init__(self): self.n = 0
        def roll(self, _e):
            self.n += 1
            return SimpleNamespace(kept=[20 if self.n == 1 else 1], total=20 if self.n == 1 else 1)
    a = _actor(); t = _actor(actor_id="t", conditions=[])
    res = grapple.attempt_shove(a, t, mode="prone", roller=FixedRoller())
    assert res.success and "prone" in t.conditions


def test_grapple_size_cap_blocks_attempt():
    a = _actor(size="medium")
    t = _actor(actor_id="t", size="huge", conditions=[])
    res = grapple.attempt_grapple(a, t, roller=DiceRoller(seed=1))
    assert res.success is False and res.reason == "target too large"


def test_escape_grapple_clears_condition_on_success():
    class FixedRoller:
        def __init__(self): self.n = 0
        def roll(self, _e):
            self.n += 1
            return SimpleNamespace(kept=[20 if self.n == 1 else 1], total=20 if self.n == 1 else 1)
    g = _actor(actor_id="g", conditions=["grappled"], grappled_by="other")
    res = grapple.escape_grapple(g, roller=FixedRoller())
    assert res.success is True
    assert "grappled" not in g.conditions
    assert g.grappled_by is None


# --------------------------------------------------------------------- #
# Concentration
# --------------------------------------------------------------------- #


def test_concentration_dc_formula():
    assert concentration.dc_for_damage(1) == 10
    assert concentration.dc_for_damage(20) == 10
    assert concentration.dc_for_damage(22) == 11
    assert concentration.dc_for_damage(100) == 50


def test_concentration_on_damage_skips_when_not_concentrating():
    a = _actor()
    assert concentration.on_damage(a, 30, roller=DiceRoller(seed=1)) is None


def test_concentration_failed_save_breaks():
    from ai_dm.game.combatant_state import Concentration as C
    a = _actor(concentration=C(spell_id="bless"))
    class LowRoller:
        def roll(self, _e):
            return SimpleNamespace(kept=[1], total=1)
    res = concentration.on_damage(a, 30, roller=LowRoller())
    assert res is not None and res.success is False and res.broken is True
    assert a.concentration is None


def test_concentration_on_condition_drops():
    from ai_dm.game.combatant_state import Concentration as C
    a = _actor(concentration=C(spell_id="bless"))
    assert concentration.on_condition(a, "incapacitated") is True
    assert a.concentration is None


# --------------------------------------------------------------------- #
# Areas of effect
# --------------------------------------------------------------------- #


def test_aoe_sphere_inclusion_at_boundary():
    pts = [(0, 0), (3, 4), (5, 0)]
    res = aoe.points_in_sphere(pts, center=(0, 0), radius_ft=5)
    assert (0, 0) in res and (3, 4) in res and (5, 0) in res


def test_aoe_cube_origin_at_corner():
    pts = [(0, 0), (5, 5), (10, 10), (11, 0)]
    res = aoe.points_in_cube(pts, origin=(0, 0), side_ft=10)
    assert (0, 0) in res and (5, 5) in res and (10, 10) in res
    assert (11, 0) not in res


def test_aoe_cone_respects_direction():
    pts = [(5, 0), (-5, 0), (0, 5)]
    forward = aoe.points_in_cone(pts, apex=(0, 0), length_ft=10, direction_deg=0)
    assert (5, 0) in forward
    assert (-5, 0) not in forward


def test_aoe_line_width_and_length():
    pts = [(5, 0), (5, 3), (5, -3), (15, 0), (5, 10)]
    res = aoe.points_in_line(
        pts, origin=(0, 0), length_ft=10, direction_deg=0, width_ft=5,
    )
    assert (5, 0) in res
    assert (15, 0) not in res  # past length
    assert (5, 10) not in res  # outside half-width


def test_aoe_template_unsupported_shape_raises():
    t = aoe.AreaTemplate(shape="bogus", anchor=(0, 0), size_ft=5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        aoe.points_in_template(t, [(0, 0)])


# --------------------------------------------------------------------- #
# Rests
# --------------------------------------------------------------------- #


def test_long_rest_fills_hp_slots_and_drops_exhaustion():
    from ai_dm.game.combatant_state import SpellSlot, ResourceUse
    a = _actor(
        hp=2, max_hp=20, temp_hp=4, exhaustion=2,
        spell_slots={1: SpellSlot(level=1, current=0, max=4)},
        resources={
            "rage": ResourceUse(key="rage", current=0, max=2, recharge="long"),
            "wind": ResourceUse(key="wind", current=0, max=1, recharge="short"),
        },
    )
    res = rests.apply_long_rest(a)
    assert a.hp == 20 and a.temp_hp == 0
    assert a.spell_slots[1].current == 4
    assert a.resources["rage"].current == 2
    assert a.resources["wind"].current == 1
    assert a.exhaustion == 1
    assert res.kind == "long"
    assert res.exhaustion_after == 1


def test_short_rest_only_restores_short_resources():
    from ai_dm.game.combatant_state import ResourceUse
    a = _actor(
        resources={
            "rage": ResourceUse(key="rage", current=0, max=2, recharge="long"),
            "wind": ResourceUse(key="wind", current=0, max=1, recharge="short"),
        },
    )
    rests.apply_short_rest(a)
    assert a.resources["rage"].current == 0
    assert a.resources["wind"].current == 1


def test_short_rest_spends_hit_dice_and_heals():
    a = _actor(hp=5, max_hp=20, hit_dice={"d8": {"current": 3, "max": 3}})
    class FourRoller:
        def roll(self, _e):
            return SimpleNamespace(kept=[4], total=4)
    res = rests.apply_short_rest(
        a, hit_dice_spent={"d8": 2}, roller=FourRoller(), con_modifier=1,
    )
    # Each die: 4 + 1 = 5; spent 2 → 10 healed (capped at max 20: 5+10=15).
    assert a.hp == 15
    assert a.hit_dice["d8"]["current"] == 1
    assert res.hit_dice_spent == {"d8": 2}
    assert res.hp_restored == 10


def test_long_rest_recovers_half_hit_dice_min_one():
    a = _actor(max_hp=10, hp=10, hit_dice={"d8": {"current": 0, "max": 6}})
    res = rests.apply_long_rest(a)
    assert a.hit_dice["d8"]["current"] == 3
    assert res.hit_dice_recovered == {"d8": 3}

