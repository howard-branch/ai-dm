"""Tests for the spell-targeting model.

Covers:
* :class:`TargetSpec` parsing from the catalog (explicit + inferred).
* :func:`resolve_targets` for the MVP shapes (``self`` / ``single`` /
  ``radius``) and the soft failure for unimplemented kinds.
* :class:`ActionResolver` integration: a ``cast_spell`` intent backed
  by the spell catalog records its resolved targets in
  ``ActionResolution.details["targets"]`` and on the caster's
  :class:`Concentration` block when concentrating.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_dm.game.combatant_state import CombatantState, Position, SpellSlot
from ai_dm.rules.action_resolver import ActionResolver
from ai_dm.rules.targeting import (
    ResolvedTargets,
    TargetSpec,
    parse_range_ft,
    resolve_targets,
)


# --------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------- #


_SPELLS_PATH = (
    Path(__file__).resolve().parents[2]
    / "assets" / "rules" / "dnd5e_spells.json"
)


@pytest.fixture(scope="module")
def spell_catalog() -> dict:
    raw = json.loads(_SPELLS_PATH.read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _hero(**overrides) -> CombatantState:
    base = dict(actor_id="morgana", name="Morgana", hp=20, max_hp=20,
                team="party", speed=30)
    base.update(overrides)
    return CombatantState(**base)


def _intent(**fields) -> SimpleNamespace:
    fields.setdefault("type", "cast_spell")
    fields.setdefault("actor_id", "morgana")
    fields.setdefault("raw_text", "cast")
    fields.setdefault("target_id", None)
    fields.setdefault("spell", None)
    return SimpleNamespace(**fields)


# --------------------------------------------------------------------- #
# parse_range_ft
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,expected",
    [("120 ft", 120), ("30ft", 30), ("60 ft", 60), ("self", None),
     ("touch", None), ("", None), (None, None)],
)
def test_parse_range_ft(raw, expected):
    assert parse_range_ft(raw) == expected


# --------------------------------------------------------------------- #
# TargetSpec.from_catalog
# --------------------------------------------------------------------- #


def test_from_catalog_explicit_block():
    spec = TargetSpec.from_catalog({"targeting": {"kind": "self"}})
    assert spec.kind == "self"
    assert spec.self_only is True
    assert spec.anchor == "caster"


def test_from_catalog_radius_block_round_trip():
    spec = TargetSpec.from_catalog({
        "targeting": {"kind": "radius", "radius_ft": 20,
                      "range_ft": 90, "anchor": "point"},
    })
    assert spec.kind == "radius"
    assert spec.radius_ft == 20
    assert spec.range_ft == 90
    assert spec.anchor == "point"


def test_from_catalog_inferred_self():
    spec = TargetSpec.from_catalog({"range": "self"})
    assert spec.kind == "self"
    assert spec.self_only is True


def test_from_catalog_inferred_single_with_range():
    spec = TargetSpec.from_catalog({"range": "120 ft"})
    assert spec.kind == "single"
    assert spec.range_ft == 120


def test_from_catalog_defaults_to_single_when_missing():
    assert TargetSpec.from_catalog(None).kind == "single"
    assert TargetSpec.from_catalog({}).kind == "single"


def test_catalog_has_chill_touch(spell_catalog):
    """The example payload (`chill_touch` → `goblin_1`) must be backed."""
    assert "chill_touch" in spell_catalog
    spec = TargetSpec.from_catalog(spell_catalog["chill_touch"])
    assert spec.kind == "single"
    assert spec.range_ft == 120


# --------------------------------------------------------------------- #
# resolve_targets — self
# --------------------------------------------------------------------- #


def test_resolve_self_returns_caster():
    actor = _hero()
    res = resolve_targets(
        TargetSpec.self_(), intent=_intent(spell="shield"), actor=actor,
    )
    assert res.success
    assert res.actor_ids == ["morgana"]


def test_resolve_self_rejects_foreign_target():
    actor = _hero()
    res = resolve_targets(
        TargetSpec.self_(),
        intent=_intent(spell="shield", target_id="goblin"),
        actor=actor,
    )
    assert res.success is False
    assert "self-only" in (res.error or "")


def test_resolve_self_accepts_self_target():
    actor = _hero()
    res = resolve_targets(
        TargetSpec.self_(),
        intent=_intent(spell="shield", target_id="morgana"),
        actor=actor,
    )
    assert res.success and res.actor_ids == ["morgana"]


# --------------------------------------------------------------------- #
# resolve_targets — single
# --------------------------------------------------------------------- #


def test_resolve_single_returns_target():
    res = resolve_targets(
        TargetSpec.single(range_ft=120),
        intent=_intent(spell="chill_touch", target_id="goblin_1"),
        actor=_hero(),
    )
    assert res.success
    assert res.actor_ids == ["goblin_1"]


def test_resolve_single_requires_target_id():
    res = resolve_targets(
        TargetSpec.single(),
        intent=_intent(spell="chill_touch"),
        actor=_hero(),
    )
    assert res.success is False
    assert "target_id" in (res.error or "")


def test_resolve_single_falls_back_to_ctx_target_id():
    res = resolve_targets(
        TargetSpec.single(),
        intent=_intent(spell="chill_touch"),
        ctx={"target_id": "goblin_2"},
        actor=_hero(),
    )
    assert res.success and res.actor_ids == ["goblin_2"]


# --------------------------------------------------------------------- #
# resolve_targets — radius
# --------------------------------------------------------------------- #


def test_resolve_radius_with_explicit_anchor_and_affected_ids():
    spec = TargetSpec.radius(20, range_ft=90, anchor="point")
    res = resolve_targets(
        spec,
        intent=_intent(spell="sleep"),
        ctx={
            "anchor": {"x": 100, "y": 200, "scene_id": "tavern"},
            "affected_ids": ["goblin_1", "goblin_2"],
        },
        actor=_hero(),
    )
    assert res.success
    assert res.actor_ids == ["goblin_1", "goblin_2"]
    assert res.anchor == {"x": 100.0, "y": 200.0, "scene_id": "tavern"}


def test_resolve_radius_anchored_on_target_actor():
    actor = _hero(position=Position(x=0, y=0))
    goblin = _hero(actor_id="goblin_1", name="G1",
                   position=Position(x=50, y=0), team="foe")
    bystander = _hero(actor_id="orc", name="Orc",
                      position=Position(x=400, y=0), team="foe")

    actors = {a.actor_id: a for a in (actor, goblin, bystander)}

    res = resolve_targets(
        TargetSpec.radius(20, anchor="target"),
        intent=_intent(spell="sleep", target_id="goblin_1"),
        ctx={
            "candidate_ids": ["goblin_1", "orc"],
            "pixels_per_foot": 5.0,  # 100 px = 20 ft → 20-ft radius == 100 px
        },
        actor=actor,
        actor_lookup=actors.get,
    )
    assert res.success
    assert res.actor_ids == ["goblin_1"]  # orc is 400 px away → 80 ft, out
    assert res.anchor == {"x": 50.0, "y": 0.0}


def test_resolve_radius_needs_anchor_or_target():
    res = resolve_targets(
        TargetSpec.radius(20, anchor="point"),
        intent=_intent(spell="sleep"),
        actor=_hero(),
    )
    assert res.success is False
    assert "anchor" in (res.error or "")


# --------------------------------------------------------------------- #
# resolve_targets — unsupported kinds fail soft
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", ["cone", "line", "cube", "sphere", "multi"])
def test_resolve_unsupported_kind_fails_soft(kind):
    res = resolve_targets(
        TargetSpec(kind=kind),
        intent=_intent(spell="x"),
        actor=_hero(),
    )
    assert isinstance(res, ResolvedTargets)
    assert res.success is False
    assert "not yet supported" in (res.error or "")


# --------------------------------------------------------------------- #
# ActionResolver integration
# --------------------------------------------------------------------- #


def _resolver_with_catalog(actor: CombatantState, catalog: dict):
    actors = {actor.actor_id: actor}
    return ActionResolver(
        actor_lookup=actors.get,
        spell_catalog=catalog,
    )


def test_action_resolver_records_targets_for_chill_touch(spell_catalog):
    """The user-facing example payload should round-trip cleanly."""
    morgana = _hero(spell_slots={1: SpellSlot(level=1, current=1, max=1)})
    resolver = _resolver_with_catalog(morgana, spell_catalog)

    # Mirrors: {"type": "cast_spell", "caster": "morgana",
    #          "spell": "chill_touch", "target": "goblin_1"}
    res = resolver.resolve_intent(
        _intent(spell="chill_touch", target_id="goblin_1"),
        ctx={"spell": "chill_touch", "level": 0},
    )
    assert res.success
    assert res.details["targets"] == ["goblin_1"]
    assert res.details["targeting"]["kind"] == "single"
    # Cantrip: no slot spent.
    assert morgana.spell_slots[1].current == 1


def test_action_resolver_self_only_rejects_foreign_target(spell_catalog):
    morgana = _hero(spell_slots={1: SpellSlot(level=1, current=1, max=1)})
    resolver = _resolver_with_catalog(morgana, spell_catalog)
    res = resolver.resolve_intent(
        _intent(spell="shield", target_id="goblin_1"),
        ctx={"spell": "shield", "level": 1, "casting_time": "reaction"},
    )
    assert res.success is False
    assert "self-only" in res.summary
    # Slot must NOT be spent on a rejected cast.
    assert morgana.spell_slots[1].current == 1
    assert morgana.reaction_used is False


def test_action_resolver_records_concentration_targets(spell_catalog):
    morgana = _hero(spell_slots={1: SpellSlot(level=1, current=1, max=1)})
    resolver = _resolver_with_catalog(morgana, spell_catalog)
    res = resolver.resolve_intent(
        _intent(spell="hex", target_id="goblin_1"),
        ctx={"spell": "hex", "level": 1, "casting_time": "bonus",
             "concentration": True},
    )
    assert res.success
    assert morgana.concentration is not None
    assert morgana.concentration.target_ids == ["goblin_1"]


def test_action_resolver_falls_back_when_no_catalog():
    """Without a wired catalog the resolver must not regress legacy casts."""
    morgana = _hero(spell_slots={1: SpellSlot(level=1, current=1, max=1)})
    resolver = ActionResolver(
        actor_lookup={morgana.actor_id: morgana}.get,
    )
    res = resolver.resolve_intent(
        _intent(spell="bless"),
        ctx={"spell": "bless", "level": 1},
    )
    assert res.success
    assert "targeting" not in res.details

