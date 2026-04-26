"""Built-in trigger actions.

Each action is a callable ``(payload, ctx) -> None`` constructed from a
declarative spec. Side effects go through the supplied collaborators
(event bus, command router, state store) so triggers are testable.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from ai_dm.ai.schemas import Command as AICommand

logger = logging.getLogger("ai_dm.triggers.actions")

Action = Callable[[dict[str, Any], dict[str, Any]], None]


def publish_event(event_bus, name: str, payload: dict | None = None) -> Action:
    payload = payload or {}

    def _a(ev_payload: dict, ctx: dict) -> None:
        merged = {**payload, "trigger_payload": ev_payload}
        event_bus.publish(name, merged)

    return _a


def set_flag(flags: dict[str, Any], key: str, value: Any) -> Action:
    def _a(_payload: dict, _ctx: dict) -> None:
        flags[key] = value

    return _a


def run_commands(command_router, commands: list[dict]) -> Action:
    def _a(_payload: dict, _ctx: dict) -> None:
        ai_commands = [AICommand.model_validate(c) for c in commands]
        try:
            command_router.dispatch(ai_commands)
        except Exception as exc:  # noqa: BLE001
            logger.warning("trigger command dispatch failed: %s", exc)

    return _a


def speak(narration_bus, text: str, voice: str | None = None) -> Action:
    """Push a narration line through the audio dispatcher (if available)."""

    def _a(_payload: dict, _ctx: dict) -> None:
        try:
            narration_bus.publish(
                "narrator.output_ready",
                {
                    "narration": text,
                    "dialogue": [],
                    "voice": voice,
                    "source": "trigger",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("trigger speak failed: %s", exc)

    return _a


def start_encounter(combat, encounter_id: str, participants: list[dict]) -> Action:
    def _a(_payload: dict, _ctx: dict) -> None:
        try:
            combat.start_encounter(encounter_id, participants)
            combat.roll_initiative()
        except Exception as exc:  # noqa: BLE001
            logger.warning("trigger start_encounter failed: %s", exc)

    return _a


def roll_random_encounter(
    event_bus,
    chapters,
    table: list[dict],
    *,
    seed_key: str | None = None,
) -> Action:
    """Weighted roll over an encounter table.

    ``table`` shape::

        [
          {"weight": 5, "encounter_id": null,   "narration": "The road is quiet."},
          {"weight": 2, "encounter_id": "encounter.skeletal_bats"},
          {"weight": 1, "encounter_id": "encounter.ancient_devotees",
           "narration": "Hymns drift from the broken chapel ahead."}
        ]

    A null ``encounter_id`` means "no encounter this leg" and acts as a
    weighted no-op. When an encounter is selected the action:

      1. Looks up the encounter's display name and monsters from the
         active chapter (``ChapterService``).
      2. Publishes ``narrator.output_ready`` with a flavor line so the
         player sees that something happened on the road.
      3. Publishes ``random_encounter.rolled`` with the resolved
         encounter id + monster summary, so downstream listeners
         (combat machine, journal, save state) can react.
    """
    import random as _random

    weighted = [
        (max(0, int(e.get("weight", 1))), e)
        for e in (table or [])
        if isinstance(e, dict)
    ]
    total = sum(w for w, _ in weighted)

    def _a(payload: dict, _ctx: dict) -> None:
        if total <= 0 or not weighted:
            return
        rng = _random.Random(seed_key) if seed_key else _random
        roll = rng.randint(1, total)
        cum = 0
        chosen: dict | None = None
        for w, entry in weighted:
            cum += w
            if roll <= cum:
                chosen = entry
                break
        if chosen is None:
            return

        eid = chosen.get("encounter_id")
        narration = chosen.get("narration") or ""
        monsters: list[dict] = []
        encounter_name = ""
        scene_id = (payload or {}).get("scene_id")

        if eid and chapters is not None:
            # Look the encounter up in the current chapter (or any
            # chapter if not found there) so we can quote real
            # monster names in the flavor narration.
            try:
                for chap in chapters.all():
                    for enc in chap.encounters or []:
                        if enc.get("id") == eid:
                            encounter_name = enc.get("name") or ""
                            monsters = enc.get("monsters") or []
                            break
                    if encounter_name:
                        break
            except Exception as exc:  # noqa: BLE001
                logger.warning("random encounter lookup failed: %s", exc)

        if not narration and eid:
            mons_str = ", ".join(
                f"{m.get('count', 1)}× {m.get('name', '?')}" for m in monsters
            ) or "unseen attackers"
            label = encounter_name or eid
            narration = f"As you travel, danger emerges — {label} ({mons_str})."
        elif not narration and not eid:
            return  # silent no-op

        try:
            event_bus.publish(
                "narrator.output_ready",
                {
                    "narration": narration,
                    "dialogue": [],
                    "source": "random_encounter",
                    "scene_id": scene_id,
                    "encounter_id": eid,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("random encounter narration publish failed: %s", exc)

        if eid:
            try:
                event_bus.publish(
                    "random_encounter.rolled",
                    {
                        "encounter_id": eid,
                        "scene_id": scene_id,
                        "name": encounter_name,
                        "monsters": monsters,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("random_encounter.rolled publish failed: %s", exc)

    return _a


# ---------------------------------------------------------------------- #
# YAML factory
# ---------------------------------------------------------------------- #

def from_spec(spec: dict, *, deps: dict) -> Action:
    """Build an action from a YAML/JSON dict.

    ``deps`` provides the live collaborators (event_bus, command_router,
    flags, combat, etc.) — one Action factory per Container.
    """
    if not isinstance(spec, dict) or len(spec) != 1:
        raise ValueError(f"invalid action spec: {spec!r}")
    (op, args), = spec.items()
    args = args or {}
    if op == "publish_event":
        return publish_event(deps["event_bus"], args["name"], args.get("payload"))
    if op == "set_flag":
        return set_flag(deps["flags"], args["key"], args.get("value", True))
    if op == "run_commands":
        return run_commands(deps["command_router"], args["commands"])
    if op == "speak":
        return speak(deps["event_bus"], args["text"], args.get("voice"))
    if op == "start_encounter":
        return start_encounter(deps["combat"], args["encounter_id"], args.get("participants", []))
    if op == "roll_random_encounter":
        return roll_random_encounter(
            deps["event_bus"],
            deps.get("chapters"),
            args.get("table") or [],
            seed_key=args.get("seed_key"),
        )
    raise ValueError(f"unknown action op: {op!r}")

