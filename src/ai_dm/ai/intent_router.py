"""Wires a parsed :class:`PlayerIntent` into the rest of the system.

For mechanical intents (attack/skill_check) the rules engine resolves
them and we publish the resolution. For ``move``/``interact`` we delegate
to the :class:`CommandRouter` (translating to a Foundry command). Speech
and meta verbs are passed through as data only.
"""
from __future__ import annotations

import logging
import re
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


# Detect a target string that is really a directional move payload that
# slipped through as a free-form anchor name — e.g. "north 10 feet",
# "10 ft north", "north 10". Without this the value gets shipped to
# Foundry as ``move_actor_to "north 10 feet"`` and fails with
# "target not found on scene". The parser handles the common phrasings
# upstream, but LLM-driven structured intents and chat shortcuts can
# still produce this shape, so we normalise defensively here too.
_CARDINAL_TOKENS = (
    "northeast", "northwest", "southeast", "southwest",
    "north", "south", "east", "west",
    "ne", "nw", "se", "sw", "n", "s", "e", "w",
    "up", "down", "left", "right",
)
_DIR_DIST_RE = re.compile(
    r"^\s*(?P<cardinal>" + "|".join(_CARDINAL_TOKENS) + r")"
    r"(?:\s+(?P<dist>\d{1,3})\s*(?:ft|feet|foot|')?)?\s*$",
    re.IGNORECASE,
)
_DIST_DIR_RE = re.compile(
    r"^\s*(?P<dist>\d{1,3})\s*(?:ft|feet|foot|')?"
    r"\s+(?P<cardinal>" + "|".join(_CARDINAL_TOKENS) + r")\s*$",
    re.IGNORECASE,
)


def _normalise_directional_target(intent: PlayerIntent) -> PlayerIntent:
    """If ``target_anchor`` is really a directional payload, lift it
    into ``direction`` / ``distance_ft`` and clear the bogus anchor.
    """
    raw = (intent.target_anchor or "").strip()
    if not raw:
        return intent
    m = _DIR_DIST_RE.match(raw) or _DIST_DIR_RE.match(raw)
    if not m:
        return intent
    cardinal = m.group("cardinal").lower()
    dist_s = m.group("dist")
    try:
        dist = int(dist_s) if dist_s else intent.distance_ft
    except (TypeError, ValueError):
        dist = intent.distance_ft
    logger.info(
        "move: rewrote target_anchor=%r as direction=%s distance_ft=%s",
        raw, cardinal, dist,
    )
    return intent.model_copy(update={
        "target_anchor": None,
        "direction": cardinal,
        "distance_ft": dist,
    })


@dataclass
class IntentEnvelope:
    intent: PlayerIntent
    resolution: ActionResolution | None = None
    commands_ok: bool = True
    rejected_reason: str | None = None
    # True when the router handed mechanical resolution off to an
    # asynchronous source (e.g. a player roll dialog round-tripped
    # through Foundry). The Director uses this to skip the narrator
    # turn — narration will be re-fired by the resume callback so
    # we don't run the LLM twice (and emit a duplicate roll dialog
    # the second time).
    deferred: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.model_dump(),
            "resolution": self.resolution.to_dict() if self.resolution else None,
            "commands_ok": self.commands_ok,
            "rejected_reason": self.rejected_reason,
            "deferred": self.deferred,
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
        encounter_manager: Any = None,     # ai_dm.game.encounter_manager.EncounterManager
        combat: Any = None,                # ai_dm.game.combat_machine.CombatMachine
    ) -> None:
        self.action_resolver = action_resolver
        self.command_router = command_router
        self.event_bus = event_bus
        self.default_scene_id = default_scene_id
        self.story_planner = story_planner
        self.pack = pack
        self.client = client
        self.clock = clock
        self.encounter_manager = encounter_manager
        self.combat = combat

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
            # Auto-start the scene's authored encounter on the first
            # ``attack`` intent so the rules engine resolves against
            # real CombatantStates (with the foe's authored AC and HP)
            # instead of throw-away ActorRuleState stubs (AC 10 / HP 0
            # — the "need 0 to hit, target HP doesn't decrease" bug).
            if intent.type == "attack":
                self._maybe_autostart_encounter(intent, ctx)
                self._maybe_retarget_attack(intent, ctx)
                # If this PC chat attack should go through the player's
                # Foundry roll dialog, defer mechanical resolution until
                # the d20 comes back; PCAttackResolver will pick it up
                # on ``roll.resolved`` and finish the attack.
                if self._defer_pc_attack(intent, ctx):
                    envelope.deferred = True
                    self._publish("intent.resolved", envelope.to_dict())
                    return envelope
            envelope.resolution = self.action_resolver.resolve_intent(intent, ctx)
        elif intent.type == "move" and self.command_router is not None:
            envelope.commands_ok = self._dispatch_move(intent, ctx)
        elif intent.type == "travel" and self.command_router is not None:
            envelope.commands_ok = self._dispatch_travel(intent, ctx)
        elif intent.type == "interact" and self.command_router is not None:
            envelope.commands_ok = self._dispatch_highlight(intent)
            # Server-side safety net: if the targeted feature has an
            # authored interaction with `check` + `dc`, publish a
            # `roll.requested` event so the dispatcher fires even when
            # the LLM forgets to populate `dice_requests`.
            try:
                self._maybe_request_authored_roll(intent, ctx)
            except Exception as exc:  # noqa: BLE001
                logger.warning("authored roll lookup failed: %s", exc)
        # speak / use_item / meta / query_world / unknown: data-only

        self._publish("intent.resolved", envelope.to_dict())
        return envelope

    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Combat helpers (auto-start + target retargeting)
    # ------------------------------------------------------------------ #

    def _maybe_autostart_encounter(self, intent: PlayerIntent, ctx: dict) -> None:
        """Start the scene's authored encounter when the player swings
        and no combat is live. No-op when the dependencies aren't
        wired (unit tests) or the scene has no authored encounter.
        """
        if self.encounter_manager is None:
            return
        s = getattr(self.combat, "state", None) if self.combat is not None else None
        if s is not None and getattr(s, "phase", None) not in (None, "ended"):
            return  # encounter already in progress
        scene_id = ctx.get("scene_id") or self.default_scene_id
        if not scene_id:
            return
        try:
            eid = self.encounter_manager.start_for_scene(
                scene_id, reason="attack_intent",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto-start encounter failed: %s", exc)
            return
        if eid:
            logger.info(
                "attack intent on scene %s auto-started encounter %s",
                scene_id, eid,
            )

    # ------------------------------------------------------------------ #

    def _defer_pc_attack(self, intent: PlayerIntent, ctx: dict) -> bool:
        """If the attacker is a player-controlled combatant and we
        haven't already been handed a ``preroll_d20``, publish a
        ``roll.requested`` so the player gets the Foundry attack
        dialog (with Dice So Nice animation), and skip the immediate
        server-side resolution.

        Returns ``True`` when the attack was deferred — the caller
        must NOT also run ``action_resolver.resolve_intent`` for this
        intent. :class:`PCAttackResolver` (subscribed to
        ``roll.resolved``) finishes the attack when the d20 comes
        back and re-runs the narration loop.

        The synchronous path is kept for:
          * NPC turns (controller != "player"),
          * the Foundry "AI DM: Attack" macro (``ctx["origin"] == "macro"``),
          * any caller that has already supplied a ``preroll_d20``
            (typically the resumption hook itself).
        """
        if self.event_bus is None:
            return False
        if ctx.get("preroll_d20") is not None:
            return False
        if str(ctx.get("origin") or "").lower() == "macro":
            return False

        actor_id = intent.actor_id
        target_id = intent.target_id
        if not actor_id or not target_id:
            return False

        # Only defer for player-controlled attackers. Look up the
        # combatant via the action resolver's actor_lookup so we agree
        # with whatever the rules engine would resolve against.
        attacker = None
        try:
            if self.action_resolver is not None and self.action_resolver.actor_lookup:
                attacker = self.action_resolver.actor_lookup(actor_id)
        except Exception:  # noqa: BLE001
            attacker = None
        if attacker is None:
            return False
        controller = str(getattr(attacker, "controller", "") or "").lower()
        if controller and controller != "player":
            return False

        # Resolve the target so we can quote the AC on the dialog.
        target = None
        try:
            if self.action_resolver is not None and self.action_resolver.actor_lookup:
                target = self.action_resolver.actor_lookup(target_id)
        except Exception:  # noqa: BLE001
            target = None
        target_ac = int(getattr(target, "ac", 0) or 0) or None
        target_name = getattr(target, "name", None) or target_id

        # Pre-compute attack mod so the dialog's bonus matches what the
        # resolver will actually apply when the d20 returns.
        weapon_obj = None
        weapon_slug = getattr(intent, "weapon", None)
        if weapon_slug:
            try:
                from ai_dm.rules import weapons as _wpn
                weapon_obj = _wpn.get_weapon(str(weapon_slug))
            except Exception:  # noqa: BLE001
                weapon_obj = None
        attack_mod = 0
        try:
            derived = self.action_resolver._derive_attack_bonus(  # noqa: SLF001
                attacker, weapon_obj,
            )
            if derived is not None:
                attack_mod = int(derived)
        except Exception:  # noqa: BLE001
            pass

        formula = f"1d20{attack_mod:+d}" if attack_mod else "1d20"
        prompt = f"Attack roll vs {target_name}"
        if target_ac is not None:
            prompt += f" (AC {target_ac})"

        payload = {
            "actor_id": actor_id,
            "scene_id": ctx.get("scene_id") or self.default_scene_id,
            "roll_type": "attack",
            "key": str(weapon_slug) if weapon_slug else None,
            "ac": target_ac,
            "formula": formula,
            "prompt_text": prompt,
            "reason": f"attack {target_name}",
            "correlation": {
                "kind": "pc_attack",
                "actor_id": actor_id,
                "target_id": target_id,
                "weapon": weapon_slug,
                "scene_id": ctx.get("scene_id") or self.default_scene_id,
                "user_id": ctx.get("user_id"),
                "user_name": ctx.get("user_name"),
                "actor_name": ctx.get("actor_name"),
                "raw_text": getattr(intent, "raw_text", None),
                # Forward overrides the resolver should respect when
                # we resume (mostly for the macro path; harmless here).
                "ctx_overrides": {
                    k: ctx[k] for k in (
                        "attack_modifier", "damage_dice", "damage_bonus",
                        "damage_type", "two_handed",
                    ) if k in ctx
                },
            },
        }
        try:
            self.event_bus.publish("roll.requested", payload)
            logger.info(
                "attack deferred to player roll dialog: actor=%s target=%s "
                "ac=%s mod=%+d",
                actor_id, target_id, target_ac, attack_mod,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("defer_pc_attack publish failed: %s", exc)
            return False

    # ------------------------------------------------------------------ #

    def _maybe_retarget_attack(self, intent: PlayerIntent, ctx: dict) -> None:
        """Map a free-form ``target_id`` like ``"orc"`` / ``"Grukk"`` to
        the actual participant's ``actor_id`` (e.g. ``"mon.grukk"``)
        so the rules engine + the registry-backed lookup find the
        right CombatantState. Falls back to "the only living foe" so
        the swing still lands when the player typed a generic noun.
        """
        s = getattr(self.combat, "state", None) if self.combat is not None else None
        if s is None or not s.participants:
            logger.info(
                "intent_router.retarget_attack: no live combat state; "
                "leaving target_id=%r unchanged", intent.target_id,
            )
            return
        target_text = (intent.target_id or intent.target_anchor or "").strip()
        original_target = intent.target_id
        if target_text:
            norm = target_text.lower().lstrip("the ").strip()
            for p in s.participants:
                if p.actor_id == target_text:
                    logger.info(
                        "intent_router.retarget_attack: %r already matches "
                        "participant.actor_id", target_text,
                    )
                    return  # already matches
                names = {
                    str(getattr(p, "actor_id", "") or "").lower(),
                    str(getattr(p, "name", "") or "").lower(),
                    str(getattr(p, "stat_block_key", "") or "").lower(),
                }
                if norm in names or any(norm and norm in n for n in names if n):
                    intent.target_id = p.actor_id
                    logger.info(
                        "intent_router.retarget_attack: %r → participant "
                        "actor_id=%r (name=%r)",
                        target_text, p.actor_id, getattr(p, "name", None),
                    )
                    return
        # Last-ditch fallback: a single living foe → use it.
        actor_id = intent.actor_id
        living_foes = [
            p for p in s.participants
            if getattr(p, "team", None) == "foe"
            and int(getattr(p, "hp", 0) or 0) > 0
            and p.actor_id != actor_id
        ]
        if len(living_foes) == 1:
            intent.target_id = living_foes[0].actor_id
            logger.info(
                "intent_router.retarget_attack: fallback to sole living foe "
                "%r (was target_id=%r)",
                intent.target_id, original_target,
            )
        else:
            logger.warning(
                "intent_router.retarget_attack: could not match target=%r — "
                "%d candidate living foe(s); leaving target_id=%r",
                target_text, len(living_foes), intent.target_id,
            )

    # ------------------------------------------------------------------ #

    def _dispatch_move(self, intent: PlayerIntent, ctx: dict) -> bool:
        # Defensive: lift "north 10 feet" / "10 ft north" out of
        # ``target_anchor`` before any downstream code treats it as
        # the name of a Foundry token / anchor pin.
        intent = _normalise_directional_target(intent)
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
                         and intent.extra.get("x") is not None) \
                and not (intent.direction and intent.distance_ft):
            # Cardinal-only phrasing ("head north", "go west") with no
            # distance: try to resolve the direction as a travel exit
            # on the current node first ("head north" → north exit →
            # cross-scene travel). If no exit matches, fall back to
            # an in-scene step of DEFAULT_MOVE_FT so the move still
            # dispatches instead of being silently skipped.
            if intent.direction and self.pack is not None:
                dest_id, dest_label = self._resolve_travel_target(
                    intent.direction, scene_id,
                )
                if dest_id and dest_id != scene_id:
                    logger.info(
                        "move(direction=%s) → travel reinterpretation: "
                        "exit resolves to scene %s",
                        intent.direction, dest_id,
                    )
                    travel_intent = intent.model_copy(update={
                        "type": "travel",
                        "verb": "travel",
                        "target_id": dest_id,
                        "target_anchor": dest_label or dest_id,
                    })
                    return self._dispatch_travel(travel_intent, ctx)
            if intent.direction:
                logger.info(
                    "move: direction=%s with no distance — defaulting "
                    "to %d ft", intent.direction, DEFAULT_MOVE_FT,
                )
                intent = intent.model_copy(update={
                    "distance_ft": DEFAULT_MOVE_FT,
                })
            else:
                logger.warning(
                    "move intent skipped: no target_anchor/target_id/x "
                    "and no direction+distance (raw=%r)", intent.raw_text,
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

    # ------------------------------------------------------------------ #
    # Authored-interaction → roll request fallback
    # ------------------------------------------------------------------ #

    # Loose verb-keyword aliases so a free-form player utterance like
    # "I pray at the altar" still matches an authored interaction whose
    # canonical verb is `pray`. Keys are normalised lower-case substrings
    # we look for in the player's verb / raw text; values are the
    # canonical authored verb tokens.
    _VERB_ALIASES: dict[str, tuple[str, ...]] = {
        "pray": ("pray", "kneel", "worship", "supplicate", "petition"),
        "search": ("search", "look", "examine", "investigate", "rummage", "inspect"),
        "read": ("read", "study", "decipher"),
        "open": ("open", "unlock", "force", "pry", "break"),
        "climb": ("climb", "scale", "ascend"),
        "hide": ("hide", "sneak"),
        "listen": ("listen", "eavesdrop"),
        "talk": ("talk", "speak", "ask", "greet", "convince", "persuade"),
        "join_service": ("join_service", "attend", "participate", "join"),
    }

    def _maybe_request_authored_roll(
        self, intent: PlayerIntent, ctx: dict,
    ) -> None:
        """If ``intent`` targets an authored interactable that carries a
        ``check`` + ``dc``, publish a ``roll.requested`` event so the
        :class:`RollRequestDispatcher` fires regardless of whether the
        LLM remembered to emit a structured ``DiceRequest``.

        No-op when the pack is unavailable, the target can't be matched,
        or the matched interaction has no mechanical fields.
        """
        if self.pack is None or self.event_bus is None:
            return
        target_text = (intent.target_anchor or intent.target_id or "").strip()
        if not target_text:
            return
        scene_id = ctx.get("scene_id") or self.default_scene_id
        if not scene_id:
            return

        feature = self._find_authored_interactable(scene_id, target_text)
        if feature is None:
            return
        interactions = feature.get("interactions") or []
        if not isinstance(interactions, list) or not interactions:
            return
        match = self._match_interaction(interactions, intent)
        if match is None:
            return
        check = match.get("check")
        dc = match.get("dc")
        if not check or dc is None:
            return

        roll_type, skill = self._parse_check_string(str(check))
        if not skill:
            return

        actor_id = intent.actor_id or ctx.get("actor_id")
        feature_label = feature.get("name") or feature.get("id") or target_text
        verb_label = match.get("verb") or intent.verb or "interact"
        reason = (
            match.get("summary")
            or f"{verb_label} {feature_label}".strip()
        )
        payload = {
            "actor_id": actor_id,
            "scene_id": scene_id,
            "roll_type": roll_type,
            "key": skill,
            "dc": int(dc) if isinstance(dc, (int, float, str)) and str(dc).lstrip("-").isdigit() else dc,
            "reason": reason,
            "prompt_text": f"Make a {skill.title()} check (DC {dc}) — {reason}",
            "correlation": {
                "source": "intent_router.authored_interaction",
                "feature": feature_label,
                "verb": verb_label,
                # Forward the authored consequence text so the
                # follow-up narration turn (synthesised by
                # RollRequestDispatcher._enqueue_followup) can quote
                # or paraphrase it. Without these the LLM sees only
                # "[roll-result] skill/religion = 17 → success" and
                # has no idea what success at the *altar* should look
                # like.
                "summary": match.get("summary"),
                "on_success": match.get("on_success"),
                "on_failure": match.get("on_failure") or match.get("on_fail"),
                "grants": match.get("grants"),
                "starts_encounter": match.get("starts_encounter"),
                "ends_encounter": match.get("ends_encounter"),
                "ends_scene": match.get("ends_scene"),
                "xp": match.get("xp"),
                "raw_text": intent.raw_text,
            },
        }
        logger.info(
            "interact: authored check on %r/%s → roll.requested skill=%s dc=%s",
            feature_label, verb_label, skill, dc,
        )
        try:
            self.event_bus.publish("roll.requested", payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("publish roll.requested failed: %s", exc)

    def _find_authored_interactable(
        self, scene_id: str, target_text: str,
    ) -> dict | None:
        """Best-effort lookup of the authored feature/anchor/NPC the
        player is interacting with. Returns the raw dict (with its
        ``interactions`` list) or ``None``.
        """
        try:
            from ai_dm.app.opening_scene import (
                find_scene_anchors,
                find_scene_node,
                find_scene_npcs,
            )
        except Exception:  # noqa: BLE001
            return None

        norm = target_text.strip().lower()
        if not norm:
            return None

        def _matches(name: str | None, fid: str | None) -> bool:
            for v in (name, fid):
                if not v:
                    continue
                v_norm = str(v).strip().lower().replace("_", " ")
                if v_norm == norm or norm in v_norm or v_norm in norm:
                    return True
            return False

        # 1. Node features (the primary authoring location for things
        # like an altar with `interactions: [{verb: "pray", ...}]`).
        node = find_scene_node(self.pack, scene_id)
        for feat in (node or {}).get("features", []) or []:
            if not isinstance(feat, dict):
                continue
            if _matches(feat.get("name"), feat.get("id")):
                return feat

        # 2. Scene anchors (some packs author interactions on anchors
        # rather than node features).
        try:
            anchors = find_scene_anchors(self.pack, scene_id)
        except Exception:  # noqa: BLE001
            anchors = []
        for a in anchors or []:
            if isinstance(a, dict) and _matches(a.get("name"), a.get("id")):
                return a

        # 3. NPCs (e.g. "ask Old Beren" / "intimidate the cultist").
        try:
            npcs = find_scene_npcs(self.pack, scene_id)
        except Exception:  # noqa: BLE001
            npcs = []
        for npc in npcs or []:
            if isinstance(npc, dict) and _matches(npc.get("name"), npc.get("id")):
                return npc
        return None

    def _match_interaction(
        self, interactions: list, intent: PlayerIntent,
    ) -> dict | None:
        """Pick the authored interaction whose ``verb`` best matches the
        player's intent. Falls back to a single-entry list (the only
        authored option) when no verb info is available.
        """
        verb_norm = (intent.verb or "").strip().lower()
        raw_norm = (intent.raw_text or "").strip().lower()

        def _verb_of(ix: dict) -> str:
            return str(ix.get("verb") or "").strip().lower()

        # Exact verb match.
        if verb_norm:
            for ix in interactions:
                if isinstance(ix, dict) and _verb_of(ix) == verb_norm:
                    return ix

        # Alias match (player verb or raw text contains an alias for the
        # authored verb).
        for ix in interactions:
            if not isinstance(ix, dict):
                continue
            authored = _verb_of(ix)
            aliases = self._VERB_ALIASES.get(authored, (authored,))
            for alias in aliases:
                if not alias:
                    continue
                if alias == verb_norm or (raw_norm and alias in raw_norm):
                    return ix

        # Single authored option → use it (avoids silently dropping a
        # check when the player phrased the verb unusually but there's
        # only one thing they could mean).
        well_shaped = [ix for ix in interactions if isinstance(ix, dict) and ix.get("verb")]
        if len(well_shaped) == 1:
            return well_shaped[0]
        return None

    @staticmethod
    def _parse_check_string(check: str) -> tuple[str, str | None]:
        """Translate authored ``check`` strings to (roll_type, key).

        Examples:
            ``"int.religion"``  -> ("skill", "religion")
            ``"wis.perception"`` -> ("skill", "perception")
            ``"dex"``           -> ("ability", "dex")
            ``"str.save"``      -> ("save", "str")
            ``"con_save"``      -> ("save", "con")
        """
        s = (check or "").strip().lower()
        if not s:
            return ("skill", None)
        # Save shapes: "str_save", "save:dex", "dex.save".
        if "save" in s:
            for tok in s.replace(":", ".").replace("_", ".").split("."):
                if tok in {"str", "dex", "con", "int", "wis", "cha"}:
                    return ("save", tok)
            return ("save", None)
        # "ability.skill" pair → skill check.
        if "." in s:
            ability, _, skill = s.partition(".")
            ability = ability.strip()
            skill = skill.strip()
            if skill:
                return ("skill", skill)
            if ability in {"str", "dex", "con", "int", "wis", "cha"}:
                return ("ability", ability)
        # Bare ability key → ability check.
        if s in {"str", "dex", "con", "int", "wis", "cha"}:
            return ("ability", s)
        # Bare skill name.
        return ("skill", s)

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

