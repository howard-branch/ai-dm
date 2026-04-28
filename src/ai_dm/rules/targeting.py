"""Spell-targeting model.

A :class:`TargetSpec` describes the *shape* of a spell's targeting —
how many actors, points, or areas a single cast can affect — and
:func:`resolve_targets` turns a `cast_spell` intent into an explicit
:class:`ResolvedTargets` (the list of actor ids the spell hits, plus
the anchor for any AoE).

Supported ``kind`` values:

* ``"self"``    — the caster only.
* ``"single"``  — exactly one creature picked by ``intent.target_id``.
* ``"multi"``   — an explicit list of creatures (capped by
  ``max_targets``) drawn from ``intent.extra["target_ids"]`` /
  ``ctx["target_ids"]``.
* ``"point"``   — no actors; just resolves the map anchor for the
  caller (e.g. *fog cloud*'s drop point).
* ``"radius"``  — every creature within ``radius_ft`` of an anchor
  (alias of ``sphere`` for grid-agnostic catalog entries).
* ``"sphere"``  — same as ``radius``, anchored at a point/target.
* ``"cube"``    — axis-aligned cube of side ``size_ft`` from an origin.
* ``"cone"``    — 5e cone of length ``size_ft`` (or ``length_ft``)
  emanating from the caster/anchor along ``direction_deg``.
* ``"line"``    — line of length ``size_ft`` (or ``length_ft``) and
  ``width_ft`` along ``direction_deg``.

For the AoE shapes (``radius``/``sphere``/``cube``/``cone``/``line``),
``resolve_targets`` will:

1. honour ``ctx["affected_ids"]`` when present (caller-supplied list);
2. otherwise expand geometrically over ``ctx["candidate_ids"]`` using
   the helpers in :mod:`ai_dm.rules.areas_of_effect`, converting via
   ``ctx["pixels_per_foot"]``;
3. fall back to anchor-only output when neither is available, so the
   caller can fan out the AoE later.

Geometry caveat
---------------
``CombatantState.position`` is stored in Foundry pixel coords, but
catalog distances are in feet. AoE expansion converts via
``ctx["pixels_per_foot"]`` (skipped, with anchor-only output, when
absent). The geometry primitives themselves live in
:mod:`ai_dm.rules.areas_of_effect`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict

TargetKind = Literal[
    "self",
    "single",
    "multi",
    "point",
    "radius",
    "sphere",
    "cube",
    "cone",
    "line",
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
    # Geometric size for cube/cone/line (feet). ``length_ft`` is an
    # alias accepted by ``from_catalog`` for the same field.
    size_ft: int | None = None
    width_ft: int | None = None  # line width; defaults via AoE helper
    direction_deg: float | None = None  # cone/line orientation
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
            # ``length_ft`` is an alias of ``size_ft`` for cone/line.
            if "length_ft" in data and "size_ft" not in data:
                data["size_ft"] = data.pop("length_ft")
            else:
                data.pop("length_ft", None)
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

    @classmethod
    def sphere(
        cls, radius_ft: int, *, range_ft: int | None = None,
        anchor: AnchorKind = "point",
    ) -> "TargetSpec":
        return cls(kind="sphere", radius_ft=radius_ft, range_ft=range_ft, anchor=anchor)

    @classmethod
    def cube(
        cls, side_ft: int, *, range_ft: int | None = None,
        anchor: AnchorKind = "point",
    ) -> "TargetSpec":
        return cls(kind="cube", size_ft=side_ft, range_ft=range_ft, anchor=anchor)

    @classmethod
    def cone(
        cls, length_ft: int, *, range_ft: int | None = None,
        anchor: AnchorKind = "caster", direction_deg: float | None = None,
    ) -> "TargetSpec":
        return cls(
            kind="cone", size_ft=length_ft, range_ft=range_ft,
            anchor=anchor, direction_deg=direction_deg,
        )

    @classmethod
    def line(
        cls, length_ft: int, *, width_ft: int | None = None,
        range_ft: int | None = None, anchor: AnchorKind = "caster",
        direction_deg: float | None = None,
    ) -> "TargetSpec":
        return cls(
            kind="line", size_ft=length_ft, width_ft=width_ft,
            range_ft=range_ft, anchor=anchor, direction_deg=direction_deg,
        )

    @classmethod
    def multi(
        cls, max_targets: int, *, range_ft: int | None = None,
    ) -> "TargetSpec":
        return cls(kind="multi", max_targets=max_targets, range_ft=range_ft)

    @classmethod
    def point(
        cls, *, range_ft: int | None = None,
    ) -> "TargetSpec":
        return cls(kind="point", range_ft=range_ft, anchor="point")


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


def _resolve_aoe(
    spec: TargetSpec,
    *,
    kind: str,
    caster: Any,
    intent: Any,
    ctx: dict,
    actor_lookup: Callable[[str], Any] | None,
) -> ResolvedTargets:
    """Shared expansion path for radius/sphere/cube/cone/line.

    Dispatches to the geometry helpers in
    :mod:`ai_dm.rules.areas_of_effect` once we have an anchor, the
    candidate ids, and a pixels-per-foot conversion. Falls back to
    ``ctx["affected_ids"]`` (caller-supplied list), or to an
    anchor-only result that downstream code can fan out later.
    """
    from ai_dm.rules.areas_of_effect import (
        points_in_cone,
        points_in_cube,
        points_in_line,
        points_in_sphere,
    )

    anchor_xy = _anchor_point(
        spec, caster=caster, intent=intent, ctx=ctx, actor_lookup=actor_lookup,
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
    candidates = ctx.get("candidate_ids")
    ppf = float(ctx.get("pixels_per_foot") or 0.0)
    size_ft = spec.size_ft
    radius_ft = spec.radius_ft if spec.radius_ft is not None else size_ft
    direction_deg = float(
        spec.direction_deg
        if spec.direction_deg is not None
        else ctx.get("direction_deg", 0.0)
    )

    if (
        anchor_xy is not None
        and isinstance(candidates, list)
        and actor_lookup is not None
        and ppf > 0.0
    ):
        # Build (id, position-in-feet) pairs for the candidates.
        anchor_ft = (anchor_xy[0] / ppf, anchor_xy[1] / ppf)
        cand_pts: list[tuple[str, tuple[float, float]]] = []
        for cid in candidates:
            cand = actor_lookup(str(cid))
            cpos = _position_of(cand)
            if cpos is None:
                continue
            cand_pts.append((str(cid), (cpos[0] / ppf, cpos[1] / ppf)))
        pts = [p for _, p in cand_pts]

        hit_pts: list[tuple[float, float]] = []
        if kind in ("radius", "sphere") and radius_ft:
            hit_pts = points_in_sphere(pts, center=anchor_ft, radius_ft=float(radius_ft))
        elif kind == "cube" and size_ft:
            hit_pts = points_in_cube(pts, origin=anchor_ft, side_ft=float(size_ft))
        elif kind == "cone" and size_ft:
            hit_pts = points_in_cone(
                pts, apex=anchor_ft, length_ft=float(size_ft),
                direction_deg=direction_deg,
            )
        elif kind == "line" and size_ft:
            hit_pts = points_in_line(
                pts, origin=anchor_ft, length_ft=float(size_ft),
                direction_deg=direction_deg,
                width_ft=float(spec.width_ft) if spec.width_ft is not None else None,
            )

        hit_set = {p for p in hit_pts}
        ids = [cid for cid, p in cand_pts if p in hit_set]
        return ResolvedTargets(spec=spec, actor_ids=ids, anchor=anchor_payload)

    # Anchor-only fallback — caller fan-outs the AoE later.
    target_id = _intent_field(intent, "target_id") or ctx.get("target_id")
    if anchor_payload is None and not target_id:
        return ResolvedTargets(
            spec=spec, actor_ids=[],
            success=False, error=f"{kind} spell needs an anchor or target_id",
        )
    return ResolvedTargets(spec=spec, actor_ids=[], anchor=anchor_payload)


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

    if kind == "multi":
        ids_in: list[Any] = []
        # Prefer explicit list on the intent / ctx.
        extra = _intent_field(intent, "extra") or {}
        if isinstance(extra, dict) and isinstance(extra.get("target_ids"), list):
            ids_in = list(extra["target_ids"])
        elif isinstance(ctx.get("target_ids"), list):
            ids_in = list(ctx["target_ids"])
        elif target_id:
            ids_in = [target_id]
        ids = [str(a) for a in ids_in if a]
        if spec.max_targets is not None:
            ids = ids[: spec.max_targets]
        if not ids:
            return ResolvedTargets(
                spec=spec, actor_ids=[],
                success=False, error="multi-target spell requires target_ids",
            )
        return ResolvedTargets(spec=spec, actor_ids=ids)

    if kind == "point":
        # Pure map-anchor selection — no actor expansion. Caller will
        # fan out the AoE template (e.g. fog cloud) over the scene.
        anchor_xy = _anchor_point(
            spec, caster=actor, intent=intent, ctx=ctx, actor_lookup=actor_lookup,
        )
        if anchor_xy is None:
            return ResolvedTargets(
                spec=spec, actor_ids=[],
                success=False, error="point-targeted spell requires an anchor",
            )
        anchor_payload: dict[str, Any] = {"x": anchor_xy[0], "y": anchor_xy[1]}
        if isinstance(ctx.get("anchor"), dict) and "scene_id" in ctx["anchor"]:
            anchor_payload["scene_id"] = ctx["anchor"]["scene_id"]
        return ResolvedTargets(spec=spec, actor_ids=[], anchor=anchor_payload)

    if kind in ("radius", "sphere", "cube", "cone", "line"):
        return _resolve_aoe(
            spec, kind=kind, caster=actor, intent=intent, ctx=ctx,
            actor_lookup=actor_lookup,
        )

    # Fallback for any future kind we haven't taught the resolver yet.
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


