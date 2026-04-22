"""Predicate DSL for trigger conditions.

Conditions are tiny composable callables that take an event payload plus
a runtime ``context`` (flags, combat snapshot, etc.) and return bool.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# (payload, context) -> bool
Predicate = Callable[[dict[str, Any], dict[str, Any]], bool]


def always() -> Predicate:
    return lambda _payload, _ctx: True


def never() -> Predicate:
    return lambda _payload, _ctx: False


def payload_eq(field: str, value: Any) -> Predicate:
    def _p(payload: dict, _ctx: dict) -> bool:
        return _dotted_get(payload, field) == value
    return _p


def payload_in(field: str, values: list[Any]) -> Predicate:
    def _p(payload: dict, _ctx: dict) -> bool:
        return _dotted_get(payload, field) in values
    return _p


def flag_eq(key: str, value: Any) -> Predicate:
    def _p(_payload: dict, ctx: dict) -> bool:
        return (ctx.get("flags") or {}).get(key) == value
    return _p


def flag_truthy(key: str) -> Predicate:
    def _p(_payload: dict, ctx: dict) -> bool:
        return bool((ctx.get("flags") or {}).get(key))
    return _p


def actor_hp_below(actor_id: str, threshold: int) -> Predicate:
    def _p(_payload: dict, ctx: dict) -> bool:
        actors = (ctx.get("actors") or {})
        actor = actors.get(actor_id) or {}
        hp = actor.get("hp")
        return hp is not None and hp < threshold
    return _p


def chapter_is(chapter_id: str) -> Predicate:
    def _p(_payload: dict, ctx: dict) -> bool:
        return ctx.get("chapter") == chapter_id
    return _p


def all_of(*preds: Predicate) -> Predicate:
    def _p(payload: dict, ctx: dict) -> bool:
        return all(pr(payload, ctx) for pr in preds)
    return _p


def any_of(*preds: Predicate) -> Predicate:
    def _p(payload: dict, ctx: dict) -> bool:
        return any(pr(payload, ctx) for pr in preds)
    return _p


def not_(pred: Predicate) -> Predicate:
    def _p(payload: dict, ctx: dict) -> bool:
        return not pred(payload, ctx)
    return _p


# ---------------------------------------------------------------------- #
# YAML-friendly construction
# ---------------------------------------------------------------------- #

@dataclass
class _Spec:
    """Internal: a parsed predicate spec."""

    fn: Predicate


def from_spec(spec: Any) -> Predicate:
    """Build a predicate from a JSON/YAML-friendly dict.

    Supported shapes::

        true / false               -> always() / never()
        {"flag_eq": {"k": "v"}}    -> flag_eq("k", "v")
        {"flag_truthy": "k"}       -> flag_truthy("k")
        {"payload_eq": {"f": v}}   -> payload_eq("f", v)
        {"actor_hp_below": {"actor_id": "g", "threshold": 5}}
        {"chapter_is": "chapter_01"}
        {"all_of": [spec, spec, ...]}
        {"any_of": [spec, spec, ...]}
        {"not": spec}
    """
    if spec is True:
        return always()
    if spec is False or spec is None:
        return never()
    if not isinstance(spec, dict) or len(spec) != 1:
        raise ValueError(f"invalid predicate spec: {spec!r}")
    (op, args), = spec.items()
    if op == "flag_eq" and isinstance(args, dict):
        ((k, v),) = args.items()
        return flag_eq(k, v)
    if op == "flag_truthy":
        return flag_truthy(args)
    if op == "payload_eq" and isinstance(args, dict):
        ((k, v),) = args.items()
        return payload_eq(k, v)
    if op == "payload_in" and isinstance(args, dict):
        ((k, vs),) = args.items()
        return payload_in(k, list(vs))
    if op == "actor_hp_below":
        return actor_hp_below(args["actor_id"], int(args["threshold"]))
    if op == "chapter_is":
        return chapter_is(args)
    if op == "all_of":
        return all_of(*(from_spec(s) for s in args))
    if op == "any_of":
        return any_of(*(from_spec(s) for s in args))
    if op == "not":
        return not_(from_spec(args))
    raise ValueError(f"unknown predicate op: {op!r}")


def _dotted_get(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur

