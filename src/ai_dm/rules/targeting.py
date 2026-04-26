"""Spell-targeting model.

A :class:`TargetSpec` describes the *shape* of a spell's targeting —
how many actors, points, or areas a single cast can affect — and
:func:`resolve_targets` turns a `cast_spell` intent into an explicit
:class:`ResolvedTargets` (the list of actor ids the spell hits, plus
the anchor for any AoE).

MVP scope (``kind`` values fully implemented):

* ``"self"``   — the caster only.
* ``"single"`` — exactly one creature picked by ``intent.target_id``.
* ``"radius"`` — every creature within ``radius_ft`` of an anchor
  (caster, target actor, or explicit ``ctx["anchor"]``).

The other shapes (``cone``, ``line``, ``sphere``, ``cube``, ``point``,
``multi``) are reserved as known kinds — they parse and round-trip
through the schema, but :func:`resolve_targets` returns
``ResolvedTargets`` with ``error="unsupported"`` so the resolver can
fail soft instead of crashing on a future spell card.

Geometry caveat
---------------
``CombatantState.position`` is in Foundry pixel coords, but ``radius_ft``
is in feet. For radius resolution we therefore prefer:

1. ``ctx["affected_ids"]`` — caller-supplied list (canonical).
2. Otherwise iterate ``actor_lookup`` candidates (``ctx["candidate_ids"]``)
   and keep those within ``radius_ft`` once converted via
   ``ctx["pixels_per_foot"]`` (defaulting to 1.0 ft/px when absent).

Anything fancier (cone/line geometry, scene-aware grids) lives in a
later milestone.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict

TargetKind = Literal[
    "self",
    "single",
    "radius",
    # Reserved (parse OK, resolve unsupported).
    "multi",
    "point",
    "cone",
    "line",
    "sphere",
    "cube",
]

AnchorKind = Literal["caster", "target", "point"]

# Pre-baked specs for the two trivial cases.
_RANGE_RE = re.compile(r"(\d+)\s*ft", re.IGNORECASE)


class TargetSpec(BaseModel):
    """Declarative description of how a spell selects its victims."""

    model_config = ConfigDict(extra="forbid")

    kind: TargetKind = "single"
    range_ft: int | None = None
    radius_ft: int | None = None
    max_targets: int | None = None
    # For radius/sphere/cube/point/cone/line — where the shape is anchored.
    # ``"caster"`` (self), ``"target"`` (the chosen creature), or
    # ``"point"`` (an explicit map coordinate provided in ctx).
    anchor: AnchorKind = "target"
    # When True, hostile/foreign target_ids are rejected (e.g. ``self``).
    self_only: bool = False
    notes: str | None = None

    # ------------------------------------------------------------------ #

    @classmethod
    def from_catalog(cls, record: dict[str, Any] | None) -> "TargetSpec":
        """Build a spec from a spell catalog entry.

        Looks at the explicit ``targeting`` block first, then falls
        back to inferring from legacy ``range`` / ``casting_time``
        strings so existing catalog entries keep working.
        """
        if not record:
            return cls(kind="single")
        block = record.get("targeting")
        if isinstance(block, dict):
            data = dict(block)
            # Tolerate legacy `kind` synonyms.
            kind = str(data.get("kind", "single")).lower().strip()
            data["kind"] = _CANONICAL_KIND.get(kind, kind)
            if data["kind"] == "self":
                data.setdefault("self_only", True)
                data.setdefault("anchor", "caster")
            return cls.model_validate(data)
        # No explicit block — infer.
        rng = str(record.get("range") or "").lower().strip()
        if rng == "self":
            return cls(kind="self", anchor="caster", self_only=True)
        range_ft = parse_range_ft(rng)
        return cls(kind="single", range_ft=range_ft)

    @classmethod
    def self_(cls) -> "TargetSpec":
        return cls(kind="self", anchor="caster", self_only=True)

    @classmethod
    def single(cls, range_ft: int | None = None) -> "TargetSpec":
        return cls(kind="single", range_ft=range_ft)

    @classmethod
    def radius(
        cls, radius_ft: int, *, range_ft: int | None = None,
        anchor: AnchorKind = "target",
    ) -> "TargetSpec":
        return cls(kind="radius", radius_ft=radius_ft, range_ft=range_ft, anchor=anchor)


_CANONICAL_KIND: dict[str, str] = {
    "self": "self",
    "single": "single",
    "creature": "single",
    "target": "single",
    "radius": "radius",
    "sphere": "sphere",
    "cube": "cube",
    "cone": "cone",
    "line": "line",
    "point": "point",
    "multi": "multi",
    "multiple": "multi",
}


# --------------------------------------------------------------------- #
# Resolved output
# --------------------------------------------------------------------- #


@dataclass
class ResolvedTargets:
    spec: TargetSpec
    actor_ids: list[str] = field(default_factory=list)
    anchor: dict[str, Any] | None = None  # {x, y, scene_id} for point anchors
    success: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.spec.kind,
            "actor_ids": list(self.actor_ids),
            "anchor": self.anchor,
            "success": self.success,
            "error": self.error,
        }


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #


def parse_range_ft(s: str | None) -> int | None:
    """Pull a feet value out of strings like ``"120 ft"`` / ``"30ft"``.

    Returns ``None`` for ``"self"``, ``"touch"``, the empty string, or
    anything we can't understand. Touch is intentionally not converted
    to ``5`` here — callers that care can special-case it.
    """
    if not s:
        return None
    m = _RANGE_RE.search(s)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _intent_field(intent: Any, name: str, default: Any = None) -> Any:
    if intent is None:
        return default
    if isinstance(intent, dict):
        return intent.get(name, default)
    return getattr(intent, name, default)


def _actor_id(actor: Any, intent: Any) -> str | None:
    return (
        _intent_field(intent, "actor_id")
        or getattr(actor, "actor_id", None)
        or "player"
    )


def _position_of(actor: Any) -> tuple[float, float] | None:
    pos = getattr(actor, "position", None)
    if pos is None:
        return None
    x = getattr(pos, "x", None)
    y = getattr(pos, "y", None)
    if x is None or y is None:
        return None
    return float(x), float(y)


def _anchor_point(
    spec: TargetSpec,
    *,
    caster: Any,
    intent: Any,
    ctx: dict,
    actor_lookup: Callable[[str], Any] | None,
) -> tuple[float, float] | None:
    """Resolve the (x, y) anchor for a radius spell."""
    if spec.anchor == "caster":
        return _position_of(caster)
    if spec.anchor == "point":
        a = ctx.get("anchor")
        if isinstance(a, dict) and "x" in a and "y" in a:
            return float(a["x"]), float(a["y"])
        return None
    # Default: anchor on the chosen target actor.
    tid = _intent_field(intent, "target_id") or ctx.get("target_id")
    if tid and actor_lookup is not None:
        tgt = actor_lookup(tid)
        if tgt is not None:
            return _position_of(tgt)
    a = ctx.get("anchor")
    if isinstance(a, dict) and "x" in a and "y" in a:
        return float(a["x"]), float(a["y"])
    return None


# --------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------- #


def resolve_targets(
    spec: TargetSpec,
    *,
    intent: Any,
    ctx: dict | None = None,
    actor: Any = None,
    actor_lookup: Callable[[str], Any] | None = None,
) -> ResolvedTargets:
    """Project ``spec`` onto a concrete list of affected actor ids.

    ``intent`` may be a :class:`PlayerIntent`, a SimpleNamespace, or a
    dict — the function reads ``actor_id`` and ``target_id`` from
    whichever shape it gets. ``ctx`` carries optional escape hatches:

    * ``target_id`` — fallback when ``intent.target_id`` is missing.
    * ``anchor`` — ``{"x", "y", "scene_id"}`` for point-anchored AoEs.
    * ``affected_ids`` — caller-supplied list of ids inside the area
      (lets callers bypass geometry while it's incomplete).
    * ``candidate_ids`` + ``pixels_per_foot`` — when present, radius
      spells iterate the candidates and keep those within range using
      simple Euclidean distance on ``actor.position``.
    """
    ctx = ctx or {}
    caster_id = _actor_id(actor, intent) or "player"
    target_id = _intent_field(intent, "target_id") or ctx.get("target_id")

    kind = spec.kind

    if kind == "self":
        if target_id and target_id != caster_id:
            return ResolvedTargets(
                spec=spec, actor_ids=[caster_id],
                success=False,
                error=f"self-only spell does not accept target {target_id!r}",
            )
        return ResolvedTargets(spec=spec, actor_ids=[caster_id])

    if kind == "single":
        if not target_id:
            return ResolvedTargets(
                spec=spec, actor_ids=[],
                success=False, error="single-target spell requires target_id",
            )
        return ResolvedTargets(spec=spec, actor_ids=[str(target_id)])

    if kind == "radius":
        anchor_xy = _anchor_point(
            spec, caster=actor, intent=intent, ctx=ctx, actor_lookup=actor_lookup,
        )
        anchor_payload: dict[str, Any] | None = None
        if anchor_xy is not None:
            anchor_payload = {"x": anchor_xy[0], "y": anchor_xy[1]}
            if isinstance(ctx.get("anchor"), dict) and "scene_id" in ctx["anchor"]:
                anchor_payload["scene_id"] = ctx["anchor"]["scene_id"]

        # Caller-supplied list short-circuits geometry.
        affected = ctx.get("affected_ids")
        if isinstance(affected, list) and affected:
            ids = [str(a) for a in affected]
            return ResolvedTargets(spec=spec, actor_ids=ids, anchor=anchor_payload)

        # Geometric expansion (best-effort, only when we have everything).
        radius_ft = spec.radius_ft
        candidates = ctx.get("candidate_ids")
        ppf = float(ctx.get("pixels_per_foot") or 0.0)
        if (
            anchor_xy is not None
            and radius_ft
            and isinstance(candidates, list)
            and actor_lookup is not None
            and ppf > 0.0
        ):
            radius_px = radius_ft * ppf
            ids: list[str] = []
            for cid in candidates:
                cand = actor_lookup(str(cid))
                cpos = _position_of(cand)
                if cpos is None:
                    continue
                dx, dy = cpos[0] - anchor_xy[0], cpos[1] - anchor_xy[1]
                if math.hypot(dx, dy) <= radius_px:
                    ids.append(str(cid))
            return ResolvedTargets(spec=spec, actor_ids=ids, anchor=anchor_payload)

        # Anchor-only result — caller will fan out the AoE later.
        if anchor_payload is None and not target_id:
            return ResolvedTargets(
                spec=spec, actor_ids=[],
                success=False, error="radius spell needs an anchor or target_id",
            )
        return ResolvedTargets(spec=spec, actor_ids=[], anchor=anchor_payload)

    # Reserved-but-unimplemented shapes.
    return ResolvedTargets(
        spec=spec, actor_ids=[],
        success=False,
        error=f"targeting kind {kind!r} not yet supported",
    )


__all__ = [
    "AnchorKind",
    "ResolvedTargets",
    "TargetKind",
    "TargetSpec",
    "parse_range_ft",
    "resolve_targets",
]


