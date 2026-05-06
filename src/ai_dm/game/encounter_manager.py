"""Encounter lifecycle facade.

Thin layer between authored encounter ids (e.g.
``"encounter.ancient_devotees"`` declared in
``chapters/*/encounters.json``) and the runtime :class:`CombatMachine`.
For now we just pipe ``start_encounter(eid)`` / ``end_encounter()``
through to the combat machine with whatever participants the authored
record describes; richer participant materialisation (NPCs from the
pack's monster index, scaling to party CR, etc.) can layer on later.

The point of this class today is to give non-combat code paths
(authored interactions, triggers, the planner) a single seam to
"start / end the encounter named X" without each one having to
re-derive participants and own the lookup against the chapter
service.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("ai_dm.game.encounters")


# "1d12+3 slashing" / "2d6 fire" / "1d8 - 1 piercing" → (dice, bonus, type)
_DAMAGE_RE = re.compile(
    r"^\s*(?P<dice>\d+\s*d\s*\d+)"
    r"\s*(?:(?P<sign>[+-])\s*(?P<bonus>\d+))?"
    r"\s*(?P<type>[a-zA-Z]+)?\s*$"
)


def _parse_damage_string(raw: str) -> tuple[str, int, str]:
    """Split an authored damage string like ``"1d12+3 slashing"`` into
    ``(dice, bonus, damage_type)``. Returns ``("1d4", 0, "")`` on a
    miss so callers always get usable defaults.
    """
    if not raw:
        return ("1d4", 0, "")
    m = _DAMAGE_RE.match(str(raw).strip())
    if not m:
        # Fall back: try to pull a leading dice expression out.
        m2 = re.match(r"^\s*(\d+\s*d\s*\d+)", str(raw))
        return (m2.group(1).replace(" ", "") if m2 else "1d4", 0, "")
    dice = m.group("dice").replace(" ", "")
    bonus = int(m.group("bonus") or 0)
    if m.group("sign") == "-":
        bonus = -bonus
    dtype = (m.group("type") or "").lower()
    return (dice, bonus, dtype)


class EncounterManager:
    def __init__(
        self,
        *,
        combat=None,                         # ai_dm.game.combat_machine.CombatMachine
        chapters=None,                       # ai_dm.services.chapter_service.ChapterService
        event_bus=None,                      # ai_dm.orchestration.event_bus.EventBus
        pack=None,                           # ai_dm.campaign.pack.CampaignPack
        turn_manager=None,                   # ai_dm.orchestration.turn_manager.TurnManager
        story_planner=None,                  # ai_dm.ai.planner.StoryPlanner
        command_router=None,                 # ai_dm.orchestration.command_router.CommandRouter
    ) -> None:
        self.combat = combat
        self.chapters = chapters
        self.event_bus = event_bus
        self.pack = pack
        self.turn_manager = turn_manager
        self.story_planner = story_planner
        self.command_router = command_router

    # ------------------------------------------------------------------ #

    def _resolve_pack_scene_id(self, scene_id: str | None) -> str | None:
        """Translate ``scene_id`` (often Foundry's opaque id like
        ``"Rqxt4PlFjW0QCbtz"``) to the pack-side scene node id
        (``"stone_chamber"``) the chapter encounters were authored
        against.

        Resolution order mirrors :class:`PromptContextBuilder`:

        1. ``scene_id`` itself if it's already a known pack scene
           id (chapter or location node).
        2. ``story_planner.state.current_scene`` — set by
           ``StoryPlanner.enter_scene`` at bootstrap and on every
           successful travel, so it tracks the *narrative* scene
           even when chat events carry an opaque Foundry id.
        3. ``pack.manifest.start.scene`` as a last resort so the
           opening room's encounter still auto-starts before the
           first travel has happened.
        """
        try:
            from ai_dm.app.opening_scene import (
                find_chapter_scene,
                find_scene_node,
            )
        except Exception:  # noqa: BLE001
            find_chapter_scene = find_scene_node = None  # type: ignore[assignment]
        if (
            self.pack is not None and scene_id and find_scene_node is not None
            and (
                find_scene_node(self.pack, scene_id) is not None
                or find_chapter_scene(self.pack, scene_id) is not None
            )
        ):
            return scene_id
        try:
            ps = getattr(self.story_planner, "state", None)
            cur = getattr(ps, "current_scene", None) if ps else None
            if cur:
                return cur
        except Exception:  # noqa: BLE001
            pass
        try:
            start = getattr(self.pack.manifest, "start", None) or {}
            start_scene = start.get("scene") if isinstance(start, dict) else None
            if start_scene:
                return start_scene
        except Exception:  # noqa: BLE001
            pass
        return scene_id

    # ------------------------------------------------------------------ #

    def find(self, encounter_id: str) -> dict | None:
        """Resolve an encounter id against every loaded chapter.

        Returns the raw authored dict (with whatever fields the pack
        declared — typically ``id``, ``monsters``, ``ambush``,
        ``description``) or ``None`` if no chapter declares it.
        """
        if self.chapters is None or not encounter_id:
            return None
        try:
            chapters = self.chapters.all()
        except Exception:  # noqa: BLE001
            return None
        for chap in chapters or []:
            for enc in (getattr(chap, "encounters", None) or []):
                if not isinstance(enc, dict):
                    continue
                if enc.get("id") == encounter_id:
                    return enc
        return None

    # ------------------------------------------------------------------ #

    def start_encounter(self, encounter_id: str, *, reason: str = "authored") -> bool:
        """Start (or no-op resume) an encounter by authored id.

        Returns True if the combat machine accepted the call. Failure
        is non-fatal — we publish ``encounter.start_failed`` so
        downstream UI can warn but the rest of the interaction
        consequence pipeline keeps running.
        """
        if self.combat is None or not encounter_id:
            if self.combat is None:
                logger.info("Starting encounter (no combat machine wired): %s", encounter_id)
            return False
        # Already running this encounter? Treat as idempotent success.
        s = getattr(self.combat, "state", None)
        if s is not None and getattr(s, "encounter_id", None) == encounter_id \
                and getattr(s, "phase", None) != "ended":
            return True
        rec = self.find(encounter_id) or {}
        participants = self._build_participants(rec)
        # Always pull the live PC + AI companions in too, so the
        # player can be targeted (NPC turn driver) and the player's
        # attacks resolve against real CombatantStates instead of
        # throw-away stubs.
        participants.extend(self._build_party_participants())
        try:
            self.combat.start_encounter(encounter_id, participants)
            # Project NPC participants onto the Foundry canvas BEFORE
            # initiative kicks off. Without this, ``apply_damage`` for
            # "Grukk" later falls through Foundry's name lookup with
            # "no actor named Grukk on the canvas" and the player sees
            # the orc shrug off every hit.
            try:
                self._spawn_npcs_into_foundry(rec, participants)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "encounter %s: NPC spawn into Foundry failed: %s",
                    encounter_id, exc,
                )
            # Roll initiative + begin round 1 + highlight the first
            # actor. Without this the encounter sits in ``idle``, no
            # ``combat.actor_highlighted`` is ever published, and the
            # NPC turn driver never wakes up — i.e. monsters never
            # fight back.
            if self.turn_manager is not None:
                try:
                    self.turn_manager.kickoff()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("turn_manager.kickoff failed: %s", exc)
            logger.info(
                "encounter %s started (%d participant(s), reason=%s)",
                encounter_id, len(participants), reason,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("start_encounter(%s) failed: %s", encounter_id, exc)
            self._publish("encounter.start_failed", {
                "encounter_id": encounter_id, "error": str(exc),
            })
            return False

    def find_for_scene(self, scene_id: str | None) -> dict | None:
        """Return the first authored encounter whose ``scene_id``
        matches ``scene_id``. Used for "auto-start the encounter for
        this scene when the player attacks" — without it, free-text
        ``attack orc`` resolves against an empty combat state and
        Grukk's HP / AC default to ActorRuleState stubs.

        Translates Foundry's opaque scene id to the authored pack
        scene id first (otherwise ``"Rqxt4PlFjW0QCbtz"`` will never
        match an encounter authored against ``"stone_chamber"`` and
        every chat ``attack X`` silently falls through to the STUB
        path).
        """
        if self.chapters is None or not scene_id:
            return None
        pack_scene = self._resolve_pack_scene_id(scene_id) or scene_id
        try:
            chapters = self.chapters.all()
        except Exception:  # noqa: BLE001
            return None
        for chap in chapters or []:
            for enc in (getattr(chap, "encounters", None) or []):
                if not isinstance(enc, dict):
                    continue
                enc_scene = enc.get("scene_id")
                if enc_scene == scene_id or enc_scene == pack_scene:
                    return enc
        return None

    def start_for_scene(
        self, scene_id: str | None, *, reason: str = "auto"
    ) -> str | None:
        """Look up + start the encounter authored for ``scene_id``.
        Returns the encounter id on success (or when the scene's
        encounter is already running), ``None`` otherwise.
        """
        rec = self.find_for_scene(scene_id)
        if not rec:
            return None
        eid = str(rec.get("id") or "")
        if not eid:
            return None
        ok = self.start_encounter(eid, reason=reason)
        return eid if ok else None

    def end_encounter(
        self,
        encounter_id: str | None = None,
        *,
        reason: str = "ended",
    ) -> bool:
        """End the active encounter.

        If ``encounter_id`` is supplied and doesn't match the active
        one, this is a no-op (returns False) — that prevents an
        authored ``ends_encounter: encounter.foo`` from accidentally
        terminating an unrelated combat that just happened to be
        running.
        """
        if self.combat is None:
            return False
        s = getattr(self.combat, "state", None)
        if s is None:
            return False
        if encounter_id and s.encounter_id != encounter_id:
            logger.info(
                "ends_encounter %s ignored — active encounter is %s",
                encounter_id, s.encounter_id,
            )
            return False
        try:
            self.combat.end_encounter(reason)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("end_encounter(%s) failed: %s", encounter_id, exc)
            return False

    # ------------------------------------------------------------------ #

    def _build_participants(self, encounter_record: dict) -> list[dict]:
        """Translate an authored encounter into Participant dicts.

        Supports the most common shape::

            { "id": "encounter.x",
              "monsters": [
                {"key": "goblin", "count": 2, "hp": 7, "ac": 13,
                 "xp": 50, "cr": 0.25, "name": "Goblin",
                 "actions": [{"name": "Scimitar", "to_hit": 4,
                              "damage": "1d6+2 slashing"}]},
                ...
              ] }

        Authored ``actions`` are parsed into :class:`MonsterAction`
        dicts (``attack.to_hit`` + ``attack.damage`` /
        ``attack.damage_type``) so the :class:`NPCTurnDriver` can pick
        the monster's real weapon instead of the generic 1d4
        bludgeoning fallback.

        Anything we can't translate is dropped with a log line; the
        encounter still starts (CombatMachine accepts an empty list).
        """
        out: list[dict] = []
        for spec in (encounter_record.get("monsters") or []):
            if not isinstance(spec, dict):
                continue
            count = max(1, int(spec.get("count") or 1))
            base_name = spec.get("name") or spec.get("key") or spec.get("id") or "monster"
            base_id = spec.get("key") or spec.get("id") or "mon"
            for i in range(count):
                aid = base_id if count == 1 else f"{base_id}_{i+1}"
                out.append({
                    "actor_id": aid,
                    "name": base_name if count == 1 else f"{base_name} {i+1}",
                    "team": "foe",
                    "controller": "ai",
                    "source": "npc",
                    "hp": int(spec.get("hp") or 1),
                    "max_hp": int(spec.get("hp") or 1),
                    "ac": int(spec.get("ac") or 10),
                    "speed": int(spec.get("speed") or 30),
                    "xp": int(spec.get("xp") or 0),
                    "challenge_rating": float(spec["cr"]) if "cr" in spec else None,
                    "creature_type": (spec.get("type")
                                      or spec.get("creature_type")
                                      or None),
                    "size": spec.get("size") or None,
                    "alignment": spec.get("alignment") or None,
                    "stat_block_key": spec.get("key") or spec.get("id"),
                    "ability_mods": _ability_mods_from_scores(spec.get("abilities") or {}),
                    "saving_throws": dict(spec.get("saves") or spec.get("saving_throws") or {}),
                    "actions": _parse_authored_actions(spec.get("actions") or []),
                })
        return out

    # ------------------------------------------------------------------ #

    def _build_party_participants(self) -> list[dict]:
        """Hydrate the live PC + AI companions into participant dicts.

        Reads ``start.player_character`` + ``start.party`` from the pack
        manifest and loads each live sheet via
        :func:`ai_dm.app.bootstrap._load_character_sheet`. Returns an
        empty list if the pack is unavailable so unit tests that wire
        :class:`EncounterManager` standalone keep working.
        """
        if self.pack is None:
            return []
        try:
            from ai_dm.app.bootstrap import _load_character_sheet, _party_members
            from ai_dm.game.combatant_state import CombatantState
        except Exception:  # noqa: BLE001
            return []

        members: list[dict] = []
        seen: set[str] = set()
        try:
            start = getattr(self.pack.manifest, "start", None) or {}
            pc_id = (start or {}).get("player_character")
            if pc_id and pc_id not in seen:
                members.append({"id": pc_id, "controller": "player"})
                seen.add(pc_id)
            for m in _party_members(self.pack):
                cid = m.get("id")
                if cid and cid not in seen:
                    members.append({**m, "id": cid})
                    seen.add(cid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("party participant lookup failed: %s", exc)
            return []

        out: list[dict] = []
        for m in members:
            cid = m["id"]
            try:
                sheet = _load_character_sheet(self.pack, cid)
            except Exception:  # noqa: BLE001
                sheet = None
            if not sheet:
                continue
            controller = m.get("controller") or sheet.get("controller") or "player"
            try:
                cs = CombatantState.from_pc_sheet(
                    sheet,
                    team="party",
                    controller=controller if controller in ("player", "ai", "gm") else "player",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("from_pc_sheet failed for %s: %s", cid, exc)
                continue
            out.append(cs.model_dump())
        return out

    # ------------------------------------------------------------------ #
    # Foundry projection: spawn NPC actors + tokens on encounter start.
    # ------------------------------------------------------------------ #

    def _spawn_npcs_into_foundry(
        self, encounter_record: dict, participants: list[dict],
    ) -> None:
        """Create a Foundry actor + token for every NPC participant.

        Mirrors :func:`ai_dm.app.bootstrap.build_party_spawn_commands`
        for the foe side of the table:

          * One :class:`CreateActorCommand` per NPC (idempotent — skipped
            if the registry already knows the participant id as an
            alias) with ``aliases=[participant.actor_id]`` so later
            ``apply_damage(actor_id="mon.grukk")`` resolves through
            ``CommandValidator`` to the real Foundry actor id.
          * One :class:`SpawnTokenCommand` per NPC, fanned out around
            ``_spawn_position`` so multi-monster encounters don't stack
            on a single tile.

        Runs through ``command_router.validator`` + ``executor`` (not
        ``router.dispatch``) because :class:`CommandRouter` doesn't
        coerce ``create_actor`` / ``spawn_token`` from ``AICommand``.
        Same pattern :class:`IntentRouter._dispatch_party_spawn` uses
        for travel.
        """
        if self.command_router is None or self.pack is None:
            return
        validator = getattr(self.command_router, "validator", None)
        executor = getattr(self.command_router, "executor", None)
        registry = getattr(executor, "registry", None) \
            if executor is not None else None
        if validator is None or executor is None:
            logger.debug(
                "encounter spawn: command_router missing validator/executor "
                "— skipping NPC spawn"
            )
            return

        npc_specs = [p for p in participants if _is_npc_participant(p)]
        if not npc_specs:
            return

        # Resolve the scene id the encounter was authored against.
        # Validator will translate pack scene id ("stone_chamber") to
        # the Foundry opaque id at dispatch time.
        scene_id = (
            encounter_record.get("scene_id")
            or self._resolve_pack_scene_id(None)
        )
        if not scene_id:
            logger.info(
                "encounter spawn: no scene_id on encounter %s — skipping",
                encounter_record.get("id"),
            )
            return

        # Lazy imports to keep the module unit-testable without the
        # full app/bootstrap stack on the path.
        from ai_dm.models.commands import (
            CreateActorCommand,
            SpawnTokenCommand,
        )
        try:
            from ai_dm.app.bootstrap import (
                _registry_actor_known,
                _spawn_position,
            )
        except Exception:  # noqa: BLE001
            _registry_actor_known = None  # type: ignore[assignment]
            _spawn_position = None         # type: ignore[assignment]

        # Spawn anchor + a per-NPC offset so tokens don't pile up.
        try:
            container_shim = type("_C", (), {
                "registry": registry,
                "location_service": getattr(
                    self.command_router, "location_service", None,
                ),
            })()
            base_x, base_y = (
                _spawn_position(container_shim, self.pack, scene_id)
                if _spawn_position is not None else (200, 200)
            )
        except Exception:  # noqa: BLE001
            base_x, base_y = 200, 200
        # Push NPCs to the opposite side of the spawn tile from the
        # party (which spreads to +X). 200px ≈ two grid cells.
        anchor_x, anchor_y = base_x - 200, base_y

        commands: list = []
        for idx, p in enumerate(npc_specs):
            actor_id = str(p.get("actor_id") or "").strip()
            if not actor_id:
                continue
            name = str(p.get("name") or actor_id)

            # Idempotent: if the registry already knows this alias from
            # a previous encounter (or a startup spawn) just (re)spawn
            # the token; create_actor would error out as a duplicate.
            already_known = False
            if registry is not None and _registry_actor_known is not None:
                try:
                    already_known = (
                        _registry_actor_known(registry, actor_id) is not None
                    )
                except Exception:  # noqa: BLE001
                    already_known = False

            if not already_known:
                commands.append(CreateActorCommand(
                    name=name,
                    actor_type="npc",
                    system=_npc_system_payload(p),
                    items=[],
                    aliases=[actor_id],
                ))
                spawn_ref = name
            else:
                spawn_ref = actor_id

            sign = -1 if idx % 2 == 0 else 1
            offset = sign * 100 * ((idx + 1) // 2)
            commands.append(SpawnTokenCommand(
                scene_id=scene_id,
                actor_id=spawn_ref,
                x=max(0, anchor_x + offset),
                y=anchor_y,
                name=name,
            ))

        if not commands:
            return

        validated: list = []
        for cmd in commands:
            try:
                validated.append(validator.validate(cmd))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "encounter spawn: validation skipped %s (%s)",
                    getattr(cmd, "type", type(cmd).__name__), exc,
                )
        if not validated:
            return
        try:
            logger.info(
                "encounter spawn: %d Foundry command(s) for %d NPC(s) on %s",
                len(validated), len(npc_specs), scene_id,
            )
            outcome = executor.execute(validated, atomic=False)
            failures = [
                r for r in (getattr(outcome, "results", []) or [])
                if not getattr(r, "ok", True)
            ]
            if failures:
                logger.warning(
                    "encounter spawn: %d/%d step(s) failed — %s",
                    len(failures), len(validated),
                    [getattr(r, "error", None) for r in failures],
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("encounter spawn: dispatch failed: %s", exc)

    def _publish(self, event: str, payload: dict[str, Any]) -> None:
        if self.event_bus is None:
            return
        try:
            self.event_bus.publish(event, payload)
        except Exception:  # noqa: BLE001
            pass


__all__ = ["EncounterManager"]


# --------------------------------------------------------------------- #
# Authored-action → MonsterAction projection
# --------------------------------------------------------------------- #


def _ability_mods_from_scores(abilities: dict) -> dict[str, int]:
    """Convert raw ability scores (``{"str": 16, ...}``) to mods
    (``{"str": 3, ...}``). Returns ``{}`` when the input is empty so
    we don't shadow a CombatantState's existing ``ability_mods`` map.
    """
    if not isinstance(abilities, dict) or not abilities:
        return {}
    out: dict[str, int] = {}
    for key, score in abilities.items():
        try:
            out[str(key).lower()] = (int(score) - 10) // 2
        except (TypeError, ValueError):
            continue
    return out


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_") or "action"


def _parse_authored_actions(raw: list) -> list[dict]:
    """Map the campaign-pack action shape (``[{name, to_hit, damage,
    reach}]``) to :class:`MonsterAction` dicts so the NPC turn driver
    can pick a real weapon (e.g. Grukk's Greataxe at +5 / 1d12+3
    slashing) instead of the unarmed-strike fallback.
    """
    out: list[dict] = []
    for spec in (raw or []):
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("name") or "Strike")
        key = str(spec.get("key") or _slug(name))
        # Already in MonsterAction shape? Pass through.
        if isinstance(spec.get("attack"), dict):
            out.append({
                "key": key, "name": name,
                "description": str(spec.get("description") or ""),
                "attack": spec["attack"],
            })
            continue
        # Authored shorthand: pull to_hit + damage out of the flat dict.
        to_hit_raw = spec.get("to_hit") or spec.get("attack_bonus") or 0
        try:
            to_hit = int(to_hit_raw)
        except (TypeError, ValueError):
            to_hit = 0
        dmg_raw = spec.get("damage") or ""
        dmg_dice, dmg_bonus, dmg_type = _parse_damage_string(str(dmg_raw))
        # Fold the +N bonus back into the dice expression so the
        # rules engine's DiceRoller resolves it without a separate
        # ``bonus`` field on the attack payload.
        dice_expr = (
            f"{dmg_dice}{dmg_bonus:+d}" if dmg_bonus else dmg_dice
        )
        reach = spec.get("reach")
        # Best-effort melee/ranged inference from the reach string
        # ("5 ft." → melee, "30/120 ft." → ranged).
        kind = "melee"
        if isinstance(reach, str) and "/" in reach:
            kind = "ranged"
        action: dict = {
            "key": key, "name": name,
            "description": str(spec.get("description") or ""),
            "attack": {
                "kind": kind,
                "to_hit": to_hit,
                "damage": dice_expr,
                "damage_type": dmg_type or "bludgeoning",
            },
        }
        out.append(action)
    return out



# --------------------------------------------------------------------- #
# NPC participant → Foundry payload helpers
# --------------------------------------------------------------------- #


def _is_npc_participant(p: dict) -> bool:
    """True for foe / AI-controlled monster combatants. Excludes the
    party (PCs and AI companions on team=party)."""
    if not isinstance(p, dict):
        return False
    if (p.get("team") or "").lower() == "party":
        return False
    if (p.get("source") or "").lower() == "npc":
        return True
    if (p.get("controller") or "").lower() == "ai":
        # Belt-and-braces: AI-controlled, non-party = monster.
        return True
    return False


def _npc_system_payload(p: dict) -> dict:
    """Compact dnd5e ``system`` patch for a freshly-created NPC actor.

    Includes the bits Foundry needs to display HP, AC, and the bridge
    module's lightweight monster sheet (creature type, size, CR,
    alignment). Richer stat-block projection (full ability scores,
    saves, items) can layer on later.
    """
    try:
        hp_max = int(p.get("max_hp") or p.get("hp") or 1)
    except (TypeError, ValueError):
        hp_max = 1
    try:
        hp_cur = int(p.get("hp") or hp_max)
    except (TypeError, ValueError):
        hp_cur = hp_max
    try:
        ac_val = int(p.get("ac") or 10)
    except (TypeError, ValueError):
        ac_val = 10

    details: dict = {}
    ctype = p.get("creature_type")
    if ctype:
        # dnd5e expects lowercase canonical type ("humanoid",
        # "monstrosity", ...). We still pass the raw string through —
        # the bridge monster sheet renders it case-insensitively, and
        # dnd5e's own sheet falls back to ``custom`` for unknowns.
        details["type"] = {"value": str(ctype).lower(), "subtype": "",
                           "swarm": "", "custom": ""}
    if p.get("alignment"):
        details["alignment"] = str(p["alignment"])
    cr = p.get("challenge_rating")
    if cr is not None:
        try:
            details["cr"] = float(cr)
        except (TypeError, ValueError):
            pass

    traits: dict = {}
    if p.get("size"):
        # dnd5e size codes: tiny/sm/med/lg/huge/grg.
        size_map = {
            "tiny": "tiny", "small": "sm", "medium": "med",
            "large": "lg", "huge": "huge", "gargantuan": "grg",
        }
        traits["size"] = size_map.get(str(p["size"]).lower(), "med")

    payload: dict = {
        "attributes": {
            "hp": {"value": hp_cur, "max": hp_max},
            "ac": {"flat": ac_val, "calc": "flat", "value": ac_val},
        },
    }
    if details:
        payload["details"] = details
    if traits:
        payload["traits"] = traits
    return payload

