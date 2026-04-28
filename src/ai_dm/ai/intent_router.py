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
from ai_dm.rules.movement_time import (
    DEFAULT_MOVE_FT,
    in_scene_move_minutes,
    travel_minutes,
)
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
        clock: Any = None,                 # ai_dm.game.clock.Clock
    ) -> None:
        self.action_resolver = action_resolver
        self.command_router = command_router
        self.event_bus = event_bus
        self.default_scene_id = default_scene_id
        self.story_planner = story_planner
        self.pack = pack
        self.client = client
        self.clock = clock

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

        # Party-scope fan-out: "move whole party to the brink",
        # "take everyone to the door", "we head to the altar". Issue
        # one ``move_actor_to`` per party token instead of just the
        # active PC. Foundry's ``move_actor_to`` resolves each token
        # independently, so the same target string may bunch them up
        # at the same anchor — that's the desired behaviour for
        # "everyone gather at X". The acting PC is dispatched first
        # so any single-token cinematic UI still focuses on them.
        if (getattr(intent, "extra", None)
                and intent.extra.get("party_scope")):
            return self._dispatch_party_move(intent, ctx, scene_id, actor_id)

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
            distance_ft=intent.distance_ft,
            direction=intent.direction,
        )
        logger.info(
            "dispatching move_actor_to actor=%s target=%r target_id=%r "
            "distance_ft=%s direction=%s scene=%s",
            actor_id, intent.target_anchor, intent.target_id,
            intent.distance_ft, intent.direction, scene_id,
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
            else:
                self._tick_clock_for_move(intent, scene_id, ctx)
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

    def _dispatch_party_move(
        self,
        intent: PlayerIntent,
        ctx: dict,
        scene_id: str | None,
        actor_id: str,
    ) -> bool:
        """Fan a single party-scope move into one ``move_actor_to``
        per party member. Returns True if every dispatched command
        succeeded; False if any failed (the failures are also
        published as ``intent.move_failed`` events for the chat-echo
        listener).

        Before dispatching the moves we make sure every party member
        has a token on ``scene_id``. Without this, members that were
        never spawned on this scene (e.g. the player joined mid-game
        on a non-start scene, or the manifest party expanded after
        startup) cause ``move_actor_to: no token for actor X in scene
        Y`` errors. ``_spawn_party_on_scene`` is idempotent on the
        Foundry side, so re-entering an already-populated scene is a
        no-op.
        """
        member_ids = self._party_member_ids(actor_id)
        logger.info(
            "party move BEGIN: lead=%s members=%s -> target=%r target_id=%r scene=%s",
            actor_id,
            [m for m in member_ids if m != actor_id],
            intent.target_anchor,
            intent.target_id,
            scene_id,
        )

        # Ensure every party member has a token on this scene before
        # dispatching the moves. Skipped silently when no scene_id is
        # available; the per-command error will surface below.
        if scene_id:
            try:
                self._spawn_party_on_scene(scene_id, ctx, intent)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "party move: pre-spawn on scene %s failed (continuing): %s",
                    scene_id, exc,
                )

        commands: list[AICommand] = []
        member_count = len(member_ids)
        for idx, mid in enumerate(member_ids):
            commands.append(AICommand(
                type="move_actor_to",
                actor_id=mid,
                target=intent.target_anchor,
                target_id=intent.target_id,
                scene_id=scene_id,
                distance_ft=intent.distance_ft,
                direction=intent.direction,
                # Tell the Foundry side this is part of a multi-actor
                # move so it offsets each member around the
                # destination instead of stacking them on the same
                # tile (the bug "everyone lands on exactly the same
                # spot" was the singleton fall-back placing each
                # token on the side of the target nearest to its
                # current location — which is identical when the
                # party started bunched together).
                formation_index=idx,
                formation_count=member_count,
            ))
        if not commands:
            logger.warning("party move: no commands built (member_ids=%s)", member_ids)
            return False
        logger.info(
            "party move DISPATCH: %d move_actor_to command(s) for actors=%s",
            len(commands), member_ids,
        )
        try:
            outcome = self.command_router.dispatch(commands)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            logger.warning("party move dispatch failed: %s", exc)
            self._publish("intent.move_failed", {
                "actor_id": actor_id,
                "target": intent.target_anchor,
                "target_id": intent.target_id,
                "scene_id": scene_id,
                "error": str(exc),
                "scope": "party",
            })
            return False
        # Per-command result breakdown (info level so a single console
        # window shows exactly which party member moved and which
        # failed, instead of a single cryptic aggregate failure).
        results = getattr(outcome, "results", []) or []
        successes: list[str] = []
        failures: list[tuple[str, str]] = []
        for mid, r in zip(member_ids, results):
            if getattr(r, "ok", True):
                successes.append(mid)
            else:
                failures.append((mid, getattr(r, "error", None) or "unknown"))
        logger.info(
            "party move RESULT: ok=%d fail=%d | moved=%s | failed=%s",
            len(successes), len(failures), successes,
            [f"{mid}:{err}" for mid, err in failures],
        )
        if failures:
            # Player-facing message — concise, names party members but
            # not the verbose Foundry error chain (that stays on the
            # Python console via the warn log above). Without this,
            # the chat-echo listener (PlayerInputDispatcher.
            # _on_move_failed) was relaying the full per-actor stack
            # trace as in-game narration.
            failed_names = [mid for mid, _ in failures]
            short_msg = (
                f"couldn't move {', '.join(failed_names)} "
                f"({len(successes)}/{len(member_ids)} of the party arrived)"
            )
            self._publish("intent.move_failed", {
                "actor_id": actor_id,
                "target": intent.target_anchor,
                "target_id": intent.target_id,
                "scene_id": scene_id,
                "error": short_msg,
                "scope": "party",
                "moved_actor_ids": successes,
                "failed_actor_ids": failed_names,
            })
        # Treat partial success as success: the lead PC + most members
        # moved, and surfacing the per-member errors via the published
        # event lets the chat-echo listener inform the player without
        # blocking the rest of the turn.
        ok = bool(successes)
        if ok:
            self._tick_clock_for_move(intent, scene_id, ctx)
        return ok

    def _party_member_ids(self, lead_actor_id: str) -> list[str]:
        """Return [lead, *companions] ids for a party-scope action.

        Reads the campaign manifest's ``start.party`` declaration via
        the bootstrap helper. Falls back to just ``[lead_actor_id]``
        when no pack/party is configured (single-PC packs continue
        to behave like the old single-actor move).
        """
        ids: list[str] = [lead_actor_id]
        if self.pack is None:
            return ids
        try:
            from ai_dm.app.bootstrap import _party_members
            for m in _party_members(self.pack):
                cid = m.get("id")
                if cid and cid not in ids:
                    ids.append(cid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("party member lookup failed: %s", exc)
        return ids

    def _publish(self, name: str, payload: dict) -> None:
        if self.event_bus is None:
            return
        try:
            self.event_bus.publish(name, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("intent event publish failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Clock integration
    # ------------------------------------------------------------------ #

    def _tick_clock_for_move(
        self,
        intent: PlayerIntent,
        scene_id: str | None,
        ctx: dict,
    ) -> None:
        """Advance the in-game clock by the time this move would
        plausibly take. Combat-round moves return 0 minutes (the
        encounter loop handles second-grained time elsewhere); out
        of combat we always tick at least one minute so casual
        exploration visibly advances the day.
        """
        if self.clock is None:
            return
        in_combat = bool(ctx.get("in_combat") or ctx.get("encounter_active"))
        try:
            minutes = in_scene_move_minutes(
                intent.distance_ft, in_combat=in_combat,
            )
            if minutes > 0:
                reason = "move" if not in_combat else "combat_move"
                self.clock.advance(minutes, reason=reason, scene_id=scene_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("move clock advance failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Travel dispatch (cross-scene)
    # ------------------------------------------------------------------ #

    def _ensure_scene_in_foundry(self, scene_id: str) -> None:
        """Create ``scene_id`` in Foundry (and project anchor pins)
        if the registry doesn't already know it.

        The ``activate_scene`` validator resolves ``scene_id`` against
        :class:`FoundryRegistry`; an unknown key raises
        :class:`RegistryMissError` and the command is dropped. Pack
        scenes other than the start scene aren't created until the
        player travels there, so we mirror the bootstrap startup
        sequence (create_scene + create_note pins) on demand.
        """
        if not scene_id or self.command_router is None:
            return
        registry = getattr(getattr(self.command_router, "validator", None),
                           "registry", None)
        executor = getattr(self.command_router, "executor", None)
        location_service = getattr(self.command_router, "location_service", None)
        if registry is None or executor is None:
            return
        try:
            registry.resolve("scene", scene_id)
            return  # already known
        except Exception:  # noqa: BLE001
            pass

        from ai_dm.app.scene_setup import (
            build_anchor_pin_commands,
            build_create_scene,
        )

        commands = [build_create_scene(location_service, scene_id)]
        commands.extend(build_anchor_pin_commands(location_service, scene_id))
        logger.info(
            "travel: lazily creating scene %s in Foundry (%d setup command(s))",
            scene_id, len(commands),
        )
        try:
            executor.execute(commands, atomic=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("lazy create_scene(%s) failed: %s", scene_id, exc)

    def _spawn_party_on_scene(
        self,
        scene_id: str,
        ctx: dict,
        intent: PlayerIntent,
    ) -> None:
        """Place the player + AI companions on ``scene_id`` after a
        successful travel. Mirrors the bootstrap startup spawn so the
        new scene has a controlled, vision-granting token for the PC.
        """
        if not scene_id or self.command_router is None or self.pack is None:
            return
        registry = getattr(getattr(self.command_router, "validator", None),
                           "registry", None)
        executor = getattr(self.command_router, "executor", None)
        location_service = getattr(self.command_router, "location_service", None)
        if registry is None or executor is None:
            logger.debug("travel: no registry/executor — skipping party spawn")
            return

        # ``build_party_spawn_commands`` was written against the
        # bootstrap Container shape (.registry, .location_service).
        # Synthesise that shape from the CommandRouter's pieces so we
        # can reuse the helper without dragging the full container
        # into the orchestration layer.
        class _SpawnContainer:
            def __init__(self, reg, loc):
                self.registry = reg
                self.location_service = loc
        container = _SpawnContainer(registry, location_service)

        pc_id = ctx.get("actor_id") or intent.actor_id
        try:
            from ai_dm.app.bootstrap import build_party_spawn_commands
            cmds = build_party_spawn_commands(
                container, self.pack, scene_id, pc_id=pc_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("travel: build_party_spawn_commands failed: %s", exc)
            return
        if not cmds:
            return
        # Run every command through the CommandValidator first so the
        # registry translates aliases like ``pc_human`` and the
        # destination scene's pack id into Foundry's opaque ids.
        # ``executor.execute`` does NOT validate, and the JS-side
        # ``resolveActor("pc_human")`` only looks up by Foundry id /
        # display name — so without this step every SpawnTokenCommand
        # for an already-registered companion fails silently with
        # "Actor not found", and the next move on this scene errors
        # with "no token for actor pc_human in scene …".
        validator = getattr(self.command_router, "validator", None)
        validated: list = []
        for cmd in cmds:
            if validator is None:
                validated.append(cmd); continue
            try:
                validated.append(validator.validate(cmd))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "party spawn: validation skipped %s (%s)",
                    getattr(cmd, "type", type(cmd).__name__), exc,
                )
        if not validated:
            return
        try:
            logger.info(
                "travel: spawning party on %s (%d command(s))",
                scene_id, len(validated),
            )
            outcome = executor.execute(validated, atomic=False)
            failures = [r for r in (getattr(outcome, "results", []) or [])
                        if not getattr(r, "ok", True)]
            if failures:
                logger.warning(
                    "party spawn: %d/%d step(s) failed on %s — %s",
                    len(failures), len(validated), scene_id,
                    [getattr(r, "error", None) for r in failures],
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("travel: party spawn dispatch failed: %s", exc)

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

        # Ensure the destination scene exists in Foundry. The validator
        # rejects ``activate_scene`` for an unknown scene with
        # ``unknown_scene`` (RegistryMissError), so for any pack scene
        # we haven't created yet we project the create_scene + anchor
        # pins (mirroring _apply_hardcoded_start) before activating.
        try:
            self._ensure_scene_in_foundry(dest_scene_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("travel: ensure_scene(%s) failed: %s", dest_scene_id, exc)

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

        # Spawn the player + companions on the new scene. Without this
        # the destination has no controlled token, so the player has no
        # vision (Foundry warns "no token to grant visibility") and any
        # subsequent move/attack intent fails to resolve an actor.
        # ``spawnToken`` is idempotent per (scene, actor) on the JS
        # side, so re-traveling reuses the existing tokens.
        self._spawn_party_on_scene(dest_scene_id, ctx, intent)

        # Advance the in-game clock for the leg of travel. We do this
        # *before* publishing scene.entered so wandering-monster
        # triggers that key off ``time.advanced`` / ``watch.passed``
        # see the new time when they fire on entry. Per-leg distance
        # (when the pack supplies it) scales by the chosen pace; we
        # fall back to 30 minutes when no distance is known so the
        # clock still ticks visibly per move.
        if self.clock is not None:
            try:
                leg_distance_mi = self._lookup_leg_distance_mi(
                    cur_scene, dest_scene_id,
                )
                minutes = travel_minutes(leg_distance_mi)
                self.clock.advance(minutes, reason="travel", scene_id=dest_scene_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("travel clock advance failed: %s", exc)

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

    def _lookup_leg_distance_mi(
        self, from_scene: str | None, to_scene: str | None,
    ) -> float | None:
        """Best-effort lookup of the distance (in miles) for a single
        travel leg. Reads the pack's location-graph nodes; returns
        ``None`` when no distance is recorded so the caller falls
        back to the default per-leg duration.
        """
        if not to_scene or self.pack is None:
            return None
        try:
            from ai_dm.app.opening_scene import _iter_json_files
            for blob in _iter_json_files(self.pack.paths.locations, "nodes.json"):
                for node in (blob.get("nodes") or []):
                    nid = node.get("id") or ""
                    if nid != from_scene:
                        continue
                    edges = node.get("edges") or node.get("connections") or []
                    for edge in edges:
                        if not isinstance(edge, dict):
                            continue
                        if edge.get("to") != to_scene and edge.get("dest") != to_scene:
                            continue
                        dist = edge.get("distance_mi") or edge.get("miles")
                        if dist is not None:
                            return float(dist)
        except Exception as exc:  # noqa: BLE001
            logger.debug("leg distance lookup failed: %s", exc)
        return None

