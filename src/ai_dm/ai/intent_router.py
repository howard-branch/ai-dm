"""Wires a parsed :class:`PlayerIntent` into the rest of the system.

For mechanical intents (attack/skill_check) the rules engine resolves
them and we publish the resolution. For ``move``/``interact`` we delegate
to the :class:`CommandRouter` (translating to a Foundry command). Speech
and meta verbs are passed through as data only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ai_dm.ai.intent_schemas import PlayerIntent
from ai_dm.ai.schemas import Command as AICommand
from ai_dm.orchestration.command_router import CommandRouter
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.rules.action_resolver import ActionResolution, ActionResolver
from ai_dm.rules.validators import validate_intent

logger = logging.getLogger("ai_dm.intent.router")


@dataclass
class IntentEnvelope:
    intent: PlayerIntent
    resolution: ActionResolution | None = None
    commands_ok: bool = True
    rejected_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.model_dump(),
            "resolution": self.resolution.to_dict() if self.resolution else None,
            "commands_ok": self.commands_ok,
            "rejected_reason": self.rejected_reason,
        }


class IntentRouter:
    def __init__(
        self,
        *,
        action_resolver: ActionResolver,
        command_router: CommandRouter | None = None,
        event_bus: EventBus | None = None,
        default_scene_id: str | None = None,
        story_planner: Any = None,         # ai_dm.ai.planner.StoryPlanner
        pack: Any = None,                  # ai_dm.campaign.pack.CampaignPack
        client: Any = None,                # ai_dm.foundry.client.FoundryClient
    ) -> None:
        self.action_resolver = action_resolver
        self.command_router = command_router
        self.event_bus = event_bus
        self.default_scene_id = default_scene_id
        self.story_planner = story_planner
        self.pack = pack
        self.client = client

    def handle(self, intent: PlayerIntent, ctx: dict | None = None) -> IntentEnvelope:
        ok, reason = validate_intent(intent)
        if not ok:
            self._publish("intent.rejected", {"intent": intent.model_dump(), "reason": reason})
            return IntentEnvelope(intent=intent, rejected_reason=reason)

        self._publish("intent.parsed", {"intent": intent.model_dump()})

        envelope = IntentEnvelope(intent=intent)
        ctx = ctx or {}

        if intent.type in (
            "attack", "skill_check", "cast_spell", "use_item",
            "dash", "disengage", "dodge", "help", "hide", "ready", "end_turn",
        ):
            envelope.resolution = self.action_resolver.resolve_intent(intent, ctx)
        elif intent.type == "move" and self.command_router is not None:
            envelope.commands_ok = self._dispatch_move(intent, ctx)
        elif intent.type == "travel" and self.command_router is not None:
            envelope.commands_ok = self._dispatch_travel(intent, ctx)
        elif intent.type == "interact" and self.command_router is not None:
            envelope.commands_ok = self._dispatch_highlight(intent)
        # speak / use_item / meta / query_world / unknown: data-only

        self._publish("intent.resolved", envelope.to_dict())
        return envelope

    # ------------------------------------------------------------------ #

    def _dispatch_move(self, intent: PlayerIntent, ctx: dict) -> bool:
        scene_id = ctx.get("scene_id") or self.default_scene_id
        actor_id = intent.actor_id or ctx.get("actor_id")
        if not actor_id:
            logger.warning(
                "move intent skipped: no actor_id (intent=%r ctx=%r)",
                intent.model_dump(), ctx,
            )
            return False
        if not intent.target_anchor and not intent.target_id \
                and not (getattr(intent, "extra", None)
                         and intent.extra.get("x") is not None):
            logger.warning(
                "move intent skipped: no target_anchor/target_id/x "
                "(raw=%r)", intent.raw_text,
            )
            return False

        # Cross-scene fast-path: if the target resolves to a known
        # travel destination (cardinal exit on the current node, or a
        # chapter scene id/name), reinterpret as a travel intent and
        # dispatch through the scene-change pipeline. This catches
        # "head north", "go shadowgrange", "move to chapel" etc. before
        # they get sent to Foundry as in-scene token moves and fail
        # with "target not found on scene".
        target_text = (intent.target_anchor or intent.target_id or "").strip()
        if target_text and self.pack is not None:
            dest_id, dest_label = self._resolve_travel_target(target_text, scene_id)
            if dest_id and dest_id != scene_id:
                logger.info(
                    "move → travel reinterpretation: %r resolves to scene %s",
                    target_text, dest_id,
                )
                travel_intent = intent.model_copy(update={
                    "type": "travel",
                    "verb": "travel",
                    "target_id": dest_id,
                    "target_anchor": dest_label or dest_id,
                })
                return self._dispatch_travel(travel_intent, ctx)

        # Use ``move_actor_to`` so the Foundry side resolves both
        # actor → token and target name → coordinates. The legacy
        # ``move_token`` path required pre-known token ids and (x, y)
        # which the chat path can't supply.
        cmd = AICommand(
            type="move_actor_to",
            actor_id=actor_id,
            target=intent.target_anchor,
            target_id=intent.target_id,
            scene_id=scene_id,
        )
        logger.info(
            "dispatching move_actor_to actor=%s target=%r target_id=%r scene=%s",
            actor_id, intent.target_anchor, intent.target_id, scene_id,
        )
        import time as _t
        t0 = _t.monotonic()
        try:
            logger.info("move → command_router.dispatch() begin")
            outcome = self.command_router.dispatch([cmd])  # type: ignore[union-attr]
            logger.info(
                "move ← command_router.dispatch() end ok=%s in %.2fs",
                outcome.ok, _t.monotonic() - t0,
            )
            if not outcome.ok:
                # Surface the per-step error (e.g. "target not found on
                # scene X — available: a, b, c") so a downstream listener
                # can echo it back to the player in chat. Without this
                # the failure is invisible to the player — the LLM
                # narrates as if the move succeeded.
                err_msg = None
                for r in getattr(outcome, "results", []) or []:
                    if not getattr(r, "ok", True) and getattr(r, "error", None):
                        err_msg = r.error
                        break
                logger.warning(
                    "move dispatch returned ok=False; rollback_errors=%s err=%s",
                    outcome.rollback_errors, err_msg,
                )
                self._publish("intent.move_failed", {
                    "actor_id": actor_id,
                    "target": intent.target_anchor,
                    "target_id": intent.target_id,
                    "scene_id": scene_id,
                    "error": err_msg or "unknown move failure",
                })
            return outcome.ok
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "move dispatch failed after %.2fs: %s",
                _t.monotonic() - t0, exc,
            )
            self._publish("intent.move_failed", {
                "actor_id": actor_id,
                "target": intent.target_anchor,
                "target_id": intent.target_id,
                "scene_id": scene_id,
                "error": str(exc),
            })
            return False

    def _publish(self, name: str, payload: dict) -> None:
        if self.event_bus is None:
            return
        try:
            self.event_bus.publish(name, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("intent event publish failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Travel dispatch (cross-scene)
    # ------------------------------------------------------------------ #

    def _dispatch_highlight(self, intent: PlayerIntent) -> bool:
        target = intent.target_id or intent.target_anchor
        if not target:
            return False
        try:
            outcome = self.command_router.dispatch([  # type: ignore[union-attr]
                AICommand(type="highlight_object", target_id=target)
            ])
            return outcome.ok
        except Exception as exc:  # noqa: BLE001
            logger.warning("highlight dispatch failed: %s", exc)
            return False

    def _dispatch_travel(self, intent: PlayerIntent, ctx: dict) -> bool:
        """Cross-scene travel: resolve the destination to a scene id,
        emit ``activate_scene``, publish ``scene.entered`` so triggers
        and the planner advance, and push a fresh opening narration.
        """
        target = (intent.target_id or intent.target_anchor or "").strip()
        if not target:
            self._publish("intent.travel_failed", {
                "reason": "no destination", "raw": intent.raw_text,
            })
            return False
        cur_scene = ctx.get("scene_id") or self.default_scene_id
        dest_scene_id, dest_label = self._resolve_travel_target(target, cur_scene)
        if dest_scene_id is None:
            avail = self._known_destinations()
            err = f"no destination matched {target!r} — available: {', '.join(avail) or '(none)'}"
            logger.warning("travel: %s", err)
            self._publish("intent.travel_failed", {
                "raw": intent.raw_text, "target": target,
                "scene_id": cur_scene, "error": err,
                "available": avail,
            })
            return False
        if dest_scene_id == cur_scene:
            self._publish("intent.travel_noop", {
                "scene_id": cur_scene,
                "message": f"Already at {dest_label or dest_scene_id}.",
            })
            return True

        logger.info(
            "travel: %s -> %s (label=%r)",
            cur_scene, dest_scene_id, dest_label,
        )
        try:
            outcome = self.command_router.dispatch([  # type: ignore[union-attr]
                AICommand(type="activate_scene", scene_id=dest_scene_id),
            ])
        except Exception as exc:  # noqa: BLE001
            logger.warning("travel dispatch failed: %s", exc)
            self._publish("intent.travel_failed", {
                "raw": intent.raw_text, "target": target,
                "scene_id": cur_scene, "error": str(exc),
            })
            return False

        # Update planner state + fire scene.entered (triggers listen here).
        if self.story_planner is not None:
            try:
                self.story_planner.enter_scene(dest_scene_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("planner.enter_scene(%s) failed: %s", dest_scene_id, exc)
                # Fall back to publishing the event ourselves so triggers still fire.
                self._publish("scene.entered", {
                    "scene_id": dest_scene_id, "from": cur_scene,
                })
        else:
            self._publish("scene.entered", {
                "scene_id": dest_scene_id, "from": cur_scene,
            })

        # Publish a structured travel-resolved event so `chapter_01`
        # beats with `intent_type=travel, target_id=road.shadowgrange`
        # complete (planner._on_intent_resolved already records intents
        # but completion currently only handles encounter ids; the
        # `intent.resolved` published by `handle()` carries the data).
        self._publish("intent.travel_resolved", {
            "scene_id": dest_scene_id,
            "from": cur_scene,
            "label": dest_label,
        })

        # Fresh opening for the new scene (best-effort).
        if self.client is not None and self.pack is not None:
            try:
                from ai_dm.app.opening_scene import build_opening_envelope
                envelope = build_opening_envelope(
                    self.pack,
                    scene_id=dest_scene_id,
                    actor_id=ctx.get("actor_id") or intent.actor_id,
                    user_id=ctx.get("user_id"),
                )
                if envelope is not None:
                    self.client.send_event(
                        "narration", envelope,
                        event_id=f"opening:{getattr(self.pack, 'slug', '')}:{dest_scene_id}",
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("travel opening narration failed: %s", exc)

        return outcome.ok

    # ------------------------------------------------------------------ #

    def _resolve_travel_target(
        self, target: str, current_scene: str | None,
    ) -> tuple[str | None, str | None]:
        """Map a free-form travel target to (scene_id, display_label).

        Resolution order:
          1. Exit on the current node (`exits["north"]` etc.).
          2. Exact / case-insensitive scene-id match (chapter scenes).
          3. Substring match on chapter scene id or scene name.
          4. Substring match on location node id / name.
        Returns ``(None, None)`` on miss.
        """
        if self.pack is None:
            return (None, None)
        from ai_dm.app.opening_scene import find_scene_node, _iter_json_files

        norm = target.strip().lower()

        # The caller's `current_scene` is often Foundry's opaque scene
        # id (e.g. "OfawtlQgfKbzhGpl"), not a pack node id like
        # "hofsaddel_pass". Walk a small candidate list so the exits
        # lookup actually finds the authored node:
        #   1. the caller's current_scene (works when ctx already
        #      carries a pack id),
        #   2. the planner's current_scene (set by enter_scene at
        #      startup and on every successful travel),
        #   3. the manifest's start.scene as last resort.
        scene_candidates: list[str] = []
        if current_scene:
            scene_candidates.append(current_scene)
        try:
            ps = getattr(self.story_planner, "state", None)
            ps_scene = getattr(ps, "current_scene", None) if ps else None
            if ps_scene and ps_scene not in scene_candidates:
                scene_candidates.append(ps_scene)
        except Exception:  # noqa: BLE001
            pass
        try:
            start_scene = (self.pack.manifest.start or {}).get("scene")
            if start_scene and start_scene not in scene_candidates:
                scene_candidates.append(start_scene)
        except Exception:  # noqa: BLE001
            pass

        # 1. Exits on the current node (try each candidate).
        for cand in scene_candidates:
            node = find_scene_node(self.pack, cand)
            exits = ((node or {}).get("exits") or {})
            if not isinstance(exits, dict) or not exits:
                continue
            # Direction match: "north" -> exits["north"]
            if norm in {k.lower() for k in exits.keys()}:
                for k, v in exits.items():
                    if k.lower() == norm and isinstance(v, str):
                        return (v, v.replace("_", " "))
            # Destination match: target == an exit value
            for v in exits.values():
                if isinstance(v, str) and v.lower() == norm:
                    return (v, v.replace("_", " "))
            # First node with exits found — don't keep searching for
            # additional direction matches (otherwise an unrelated
            # candidate could shadow the real current-node mapping).
            break

        # 2/3. Chapter scenes.
        chapter_scenes = self._chapter_scenes()
        for sid, sname in chapter_scenes:
            if sid.lower() == norm or (sname and sname.lower() == norm):
                return (sid, sname or sid)
        for sid, sname in chapter_scenes:
            if norm in sid.lower() or (sname and norm in sname.lower()):
                return (sid, sname or sid)

        # 4. Location nodes (e.g. "shadowgrange" → shadowgrange node id).
        for blob in _iter_json_files(self.pack.paths.locations, "nodes.json"):
            for node in (blob.get("nodes") or []):
                nid = node.get("id") or ""
                nname = node.get("name") or ""
                if nid.lower() == norm or nname.lower() == norm:
                    return (nid, nname or nid)
        for blob in _iter_json_files(self.pack.paths.locations, "nodes.json"):
            for node in (blob.get("nodes") or []):
                nid = node.get("id") or ""
                nname = node.get("name") or ""
                if norm in nid.lower() or (nname and norm in nname.lower()):
                    return (nid, nname or nid)

        return (None, None)

    def _chapter_scenes(self) -> list[tuple[str, str]]:
        """Return [(scene_id, scene_name), ...] across all chapters."""
        out: list[tuple[str, str]] = []
        if self.story_planner is None:
            return out
        try:
            for chap in self.story_planner.chapters.all():
                for sc in chap.scenes:
                    out.append((sc.id, sc.name or ""))
        except Exception:  # noqa: BLE001
            pass
        return out

    def _known_destinations(self) -> list[str]:
        names: list[str] = []
        for sid, sname in self._chapter_scenes():
            names.append(sname or sid)
        # Dedupe preserving order.
        seen = set()
        out: list[str] = []
        for n in names:
            if n and n not in seen:
                seen.add(n); out.append(n)
        return out

