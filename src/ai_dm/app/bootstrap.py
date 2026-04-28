"""Application bootstrap.

Loads ``config/settings.yaml``, resolves the active campaign pack, and
wires up the :class:`Container` and :class:`Director`.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from ai_dm.app.character_wizard import (
    build_sheet,
    needs_wizard,
    run_wizard,
    sheet_path,
    write_sheet,
)
from ai_dm.app.container import Container, ContainerConfig
from ai_dm.app.lobby import wait_for_lobby_ready
from ai_dm.app.opening_scene import build_opening_envelope
from ai_dm.app.runtime import Runtime
from ai_dm.app.scene_setup import (
    build_anchor_pin_commands as _scene_anchor_pin_commands,
    build_create_scene as _scene_build_create_scene,
)
from ai_dm.app.settings import Settings
from ai_dm.campaign.pack import CampaignPack, resolve_pack, seed_characters
from ai_dm.game.state_store import StateStore
from ai_dm.models.commands import (
    ActivateSceneCommand,
    CreateActorCommand,
    CreateNoteCommand,
    CreateSceneCommand,
    GameCommand,
    SpawnTokenCommand,
    UpdateActorCommand,
)
from ai_dm.orchestration.director import Director
from ai_dm.orchestration.remote_character_wizard import RemoteCharacterWizard

logger = logging.getLogger("ai_dm.app.bootstrap")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_pack_from_settings(settings: Settings) -> CampaignPack:
    cs = settings.campaigns
    if cs.active:
        try:
            return resolve_pack(
                cs.active,
                campaigns_root=cs.root,
                state_root=cs.state_root,
            )
        except FileNotFoundError as exc:
            logger.warning(
                "active campaign %r not found: %s — falling back to legacy layout",
                cs.active, exc,
            )
    # Legacy fallback: existing assets/campaign + data/saves layout.
    return CampaignPack.from_legacy_layout(
        campaign_assets=Path("assets/campaign"),
        saves_dir=Path("data/saves"),
    )


def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or Settings.load()
    pack = _resolve_pack_from_settings(settings)
    _seed_ai_companions(pack)
    audio_enabled = _env_bool("AI_DM_AUDIO", default=True)
    edge_voice = os.environ.get("TTS_VOICE") or "en-GB-SoniaNeural"
    container = Container.build(
        ContainerConfig(pack=pack, audio_enabled=audio_enabled, edge_voice=edge_voice)
    )

    # Character creation now happens *after* the container is built so
    # the wizard can be driven through the live Foundry browser session
    # rather than the local terminal. The relay supervisor + socket
    # bridge are already up by this point.
    pending_user_bind, wizard_sheet = _maybe_run_character_wizard(pack, container)

    # Inject the active player character into the prompt context so the
    # narrator knows who's speaking. Pulled from the manifest's
    # ``start.player_character`` and the live character sheet (seeded
    # on first run by the container build, or freshly written by the
    # remote wizard above). If the wizard just ran in this process we
    # prefer its in-memory sheet so a transient disk read failure can
    # never leave the runtime announcing "[no player character loaded]".
    pc_id = (pack.manifest.start or {}).get("player_character")
    pc_sheet: dict | None = None
    if pc_id:
        pc_sheet = _load_character_sheet(pack, pc_id)
        if pc_sheet is None and wizard_sheet is not None:
            pc_sheet = wizard_sheet
            logger.info(
                "using freshly-built wizard sheet for %s (disk load returned None)",
                pc_id,
            )
    if pc_sheet and container.prompt_context is not None:
        container.prompt_context.character = pc_sheet
    elif container.prompt_context is not None:
        # Diagnostic: explain *why* the runtime banner is about to say
        # "[no player character loaded]". Without this the user only
        # sees the symptom.
        if not pc_id:
            logger.warning(
                "no character loaded: campaign manifest has no start.player_character; "
                "the runtime will start without a bound PC."
            )
        else:
            live = sheet_path(pack, pc_id)
            seed = pack.paths.characters_seed / f"{pc_id}.json"
            logger.warning(
                "no character loaded for pc_id=%s — neither the live sheet (%s, exists=%s) "
                "nor the seed (%s, exists=%s) could be read. The runtime banner will say "
                "'[no player character loaded]'. If the Foundry wizard just ran, check "
                "the previous logs for 'character wizard write failed'.",
                pc_id, live, live.exists(), seed, seed.exists(),
            )

    # Inject the rest of the party so the narrator can answer
    # "who is in my party". Excludes the active PC (already in `character`).
    if container.prompt_context is not None:
        party_brief = _build_party_brief(pack, exclude_id=pc_id)
        container.prompt_context.party = party_brief

    state_store = StateStore(base=pack.state.saves)
    director = Director(
        state_store=state_store,
        command_router=container.command_router,
        narrator=container.narrator,
        prompt_context=container.prompt_context,
        npc_memory=container.npc_memory,
        intent_parser=container.intent_parser,
        intent_router=container.intent_router,
        event_bus=container.event_bus,  # publishes narrator.output_ready
    )
    # Wire the chat-driven dispatcher to the freshly built Director so
    # ``/act <text>`` from Foundry chat runs the same turn pipeline as
    # the local REPL.
    if container.player_input_dispatcher is not None:
        container.player_input_dispatcher.wire(director)
    # Lobby gate: don't touch the world until the GM says "everyone is
    # here". Publishes a chat status every couple of seconds and blocks
    # on `/startgame` (or the AI_DM_AUTOSTART=1 env override).
    wait_for_lobby_ready(pack, container)
    _maybe_clear_chat(container)
    _maybe_reset_foundry_state(pack, container)
    _apply_hardcoded_start(pack, container)
    # Tell the planner (and any `scene.entered` triggers — `start_intro`,
    # the road random-encounter table, …) that play has begun in the
    # start scene. Without this, the chapter never advances and the
    # authored intro trigger never fires.
    _publish_initial_scene_entered(pack, container)
    # If the player just used the Foundry-side wizard, bind their user
    # to the freshly-created actor so Foundry stops asking them which
    # character to play on next login.
    if pending_user_bind and pc_id:
        _request_user_actor_binding(container, pc_id, pending_user_bind)
    # Final step: tell the player *where they are* and what they can
    # interact with. Without this the player is staring at a map with
    # no in-fiction context. Best-effort — silently no-ops if the pack
    # has no opening info or the relay isn't connected.
    _emit_opening_narration(
        pack, container, pc_id=pc_id, user_id=pending_user_bind,
    )
    return Runtime(director=director, container=container)


# --------------------------------------------------------------------- #
# Character wizard gate
# --------------------------------------------------------------------- #


def _maybe_run_character_wizard(
    pack: CampaignPack, container: Container,
) -> tuple[str | None, dict | None]:
    """Run the guided character creator if the active pack has no PC sheet.

    Forced on with ``AI_DM_NEW_CHARACTER=1``. Skipped if the manifest has
    no ``start.player_character`` or if a live sheet already exists.
    Drives the wizard through the connected Foundry browser by default;
    falls back to the legacy stdin wizard when ``AI_DM_LOCAL_WIZARD=1``.

    Returns ``(user_id, sheet)``:

      * ``user_id`` — Foundry user id of the player who answered the
        remote wizard so the caller can bind ``user.character`` after
        the actor is created. ``None`` for the local wizard, when no
        wizard ran, or when the player cancelled.
      * ``sheet`` — the freshly built sheet (already persisted to disk)
        so the caller can wire ``prompt_context.character`` directly,
        bypassing any transient disk-read hiccup. ``None`` when no
        wizard ran or it produced no sheet.
    """
    pc_id = (pack.manifest.start or {}).get("player_character")
    if not pc_id:
        return None, None
    forced = _env_bool("AI_DM_NEW_CHARACTER", default=False)
    live_sheet = sheet_path(pack, pc_id)
    if not forced and live_sheet.exists():
        return None, None
    if not forced and not needs_wizard(pack, pc_id):
        return None, None

    use_local = _env_bool("AI_DM_LOCAL_WIZARD", default=False)
    sheet: dict | None = None
    user_id: str | None = None
    if use_local or container.client is None or container.event_bus is None:
        try:
            sheet = run_wizard(pc_id, pack=pack)
        except (EOFError, KeyboardInterrupt):
            logger.warning("character wizard cancelled; continuing with existing state")
            return None, None
    else:
        logger.info(
            "no character sheet for %s — prompting via Foundry browser. "
            "Open the Foundry world and complete the dialog to continue.",
            pc_id,
        )
        wizard = RemoteCharacterWizard(
            event_bus=container.event_bus,
            client=container.client,
            pc_id=pc_id,
            pack=pack,
        )
        try:
            sheet = wizard.prompt_and_wait()
        except KeyboardInterrupt:
            logger.warning("character wizard cancelled; continuing with existing state")
            return None, None
        user_id = wizard.player_user_id

    if sheet is None:
        logger.warning("character wizard produced no sheet; continuing with existing state")
        return None, None
    try:
        path = write_sheet(pack, pc_id, sheet)
        logger.info("character wizard wrote sheet: %s", path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("character wizard write failed: %s", exc)
    return user_id, sheet


def _request_user_actor_binding(
    container: Container, pc_id: str, user_id: str,
) -> None:
    """Tell Foundry to set ``user.character`` to the newly-created actor.

    The startup sequence registers the actor under both its sheet name
    (``pc_name``) and the manifest's ``pc_id``. We resolve through the
    registry so we get the real Foundry document id, then push an
    ``assign_player_character`` event the JS bridge handles GM-side.
    """
    if container.client is None or container.registry is None:
        return
    actor_foundry_id: str | None = None
    for key in (pc_id,):
        try:
            actor_foundry_id = container.registry.resolve("actor", key)
            break
        except Exception:  # noqa: BLE001
            continue
    if actor_foundry_id is None:
        # Fall back to looking up by the sheet's display name.
        sheet = _load_character_sheet(container.pack, pc_id) if container.pack else None
        name = (sheet or {}).get("name")
        if name:
            try:
                actor_foundry_id = container.registry.resolve("actor", name)
            except Exception:  # noqa: BLE001
                actor_foundry_id = None
    if actor_foundry_id is None:
        logger.warning(
            "cannot bind user %s to actor for pc %s — no registry entry yet",
            user_id, pc_id,
        )
        return
    try:
        container.client.send_event("assign_player_character", {
            "user_id": user_id,
            "actor_id": actor_foundry_id,
            "pc_id": pc_id,
        })
        logger.info(
            "requested Foundry to bind user=%s → actor=%s (pc=%s)",
            user_id, actor_foundry_id, pc_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("assign_player_character send failed: %s", exc)


def _emit_opening_narration(
    pack: CampaignPack,
    container: Container,
    *,
    pc_id: str | None,
    user_id: str | None,
) -> None:
    """Push a "where you are" narration to Foundry chat after startup.

    Reads scene metadata from the pack (``locations/*/nodes.json``,
    ``scene_locations.json``, ``npcs.json``) and assembles a deterministic
    envelope — no LLM call. Skips silently if there's no client, no
    start scene, or no pack info about the scene.
    """
    if container.client is None:
        return
    start = pack.manifest.start or {}
    scene_id = start.get("scene")
    if not scene_id:
        return
    envelope = build_opening_envelope(
        pack, scene_id=scene_id, actor_id=pc_id, user_id=user_id,
    )
    if envelope is None:
        logger.info(
            "no opening info for scene %s in pack %s; skipping opener",
            scene_id, pack.slug,
        )
        return
    try:
        # Stable event_id so a relay replay (or accidental double-call)
        # is suppressed by the Foundry-side seenEventIds LRU.
        event_id = f"opening:{pack.slug}:{scene_id}"
        container.client.send_event("narration", envelope, event_id=event_id)
        logger.info(
            "emitted opening narration: scene=%s interactables=%d exits=%d",
            scene_id,
            len(envelope.get("interactables") or []),
            len(envelope.get("exits") or []),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("opening narration send failed: %s", exc)

    # Also publish on the in-process bus so the audio dispatcher reads
    # the opening aloud through TTS. Prose already weaves interactables
    # and exits into the narrative (see opening_scene._compose_prose),
    # so we just speak the narration verbatim — no extra list sentence.
    if container.event_bus is not None:
        spoken = (envelope.get("narration") or "").strip()
        if spoken:
            try:
                container.event_bus.publish(
                    "narrator.output_ready",
                    {
                        "narration": spoken,
                        "dialogue": [],
                        "source": "opening",
                        "scene_id": scene_id,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("opening narrator.output_ready publish failed: %s", exc)


def _join_human(names: list[str]) -> str:
    """Render ``["a","b","c"]`` as ``"a, b and c"`` for spoken text."""
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])} and {names[-1]}"


# --------------------------------------------------------------------- #
# Party seeding (AI-controlled companions)
# --------------------------------------------------------------------- #


def _party_members(pack: CampaignPack) -> list[dict]:
    """Return the declared party from the manifest, normalised to dicts.

    Empty list when the manifest has no ``start.party`` block (single-PC
    packs continue to work unchanged).
    """
    raw = (pack.manifest.start or {}).get("party") or []
    out: list[dict] = []
    for entry in raw:
        if isinstance(entry, str):
            out.append({"id": entry, "controller": "ai"})
        elif isinstance(entry, dict) and entry.get("id"):
            out.append(dict(entry))
    return out


def _seed_ai_companions(pack: CampaignPack) -> None:
    """Ensure every AI-controlled party member has a live character sheet.

    Order of resolution per companion:
      1. Live sheet already exists → leave it (idempotent).
      2. A seed sheet exists in the pack → ``seed_characters`` will copy
         it during ``_apply_hardcoded_start``; nothing to do here.
      3. Otherwise build a sheet from the manifest hints (``archetype``,
         ``background``, ``name``) using the wizard's pure builder, tag
         it as AI-controlled, and write it to state.
    """
    members = _party_members(pack)
    if not members:
        return
    for m in members:
        if m.get("controller", "ai") != "ai":
            continue
        cid = m["id"]
        if sheet_path(pack, cid).exists():
            continue
        seed = pack.paths.characters_seed / f"{cid}.json"
        if seed.exists():
            continue  # seed_characters() copies it later
        archetype = (m.get("archetype") or "warrior").lower()
        background = (m.get("background") or "sellsword").lower()
        name = m.get("name") or cid.replace("_", " ").title()
        try:
            sheet = build_sheet(cid, name, archetype, None, background, pack=pack)
        except KeyError as exc:
            logger.warning(
                "party member %s has unknown archetype/background %s; skipping",
                cid, exc,
            )
            continue
        sheet["controller"] = "ai"
        try:
            path = write_sheet(pack, cid, sheet)
            logger.info("seeded AI companion %s at %s", cid, path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to seed AI companion %s: %s", cid, exc)


# --------------------------------------------------------------------- #
# Hardcoded start (Step 1 — Morgana pack only, intentionally not generic)
# --------------------------------------------------------------------- #


def _apply_hardcoded_start(pack: CampaignPack, container: Container) -> None:
    """Activate the start scene, ensure the PC exists, and spawn its token.

    Reads ``start: {scene, player_character}`` from the pack manifest.
    Best-effort: failures (e.g. relay not connected during tests) are
    logged but never raised.
    """
    start = pack.manifest.start or {}
    scene_id = start.get("scene")
    pc_id = start.get("player_character")
    if not scene_id or not pc_id:
        logger.info("no `start` block in manifest; skipping startup sequence")
        return

    # 1. Ensure the live character file exists (idempotent copy from seed).
    try:
        seed_characters(pack)
    except Exception as exc:  # noqa: BLE001
        logger.warning("seed_characters failed: %s", exc)


    # 2. Push the start sequence to Foundry: activate scene → create
    #    actor (idempotent if already registered) → spawn token.
    executor = container.executor
    if executor is None:
        logger.info("no executor available; skipping Foundry startup writes")
        return

    # Fail-fast: if no GM-logged-in Foundry browser is connected, none
    # of these commands will succeed (only the GM has permission to
    # create/activate scenes and create actors). Surface a clear,
    # actionable message instead of letting the batch fail with a
    # cryptic "must be a GM" error from a Player browser.
    if container.client is not None:
        census = None
        # Poll briefly so a slow Foundry tab still has a chance to connect.
        import time as _time
        deadline = _time.monotonic() + 10.0
        while _time.monotonic() < deadline:
            census = container.client.who(timeout=1.0)
            if census and census.get("foundry_gm_count", 0) > 0:
                break
            _time.sleep(0.5)
        if census is None:
            logger.warning(
                "STARTUP ABORTED: relay did not respond to who() — "
                "is the Foundry world running and the AI DM Bridge module enabled?"
            )
            return
        gm_count = census.get("foundry_gm_count", 0)
        foundry_count = census.get("foundry_count", 0)
        if gm_count == 0:
            users = census.get("foundry_clients") or []
            user_str = ", ".join(
                f"{u.get('user_name')!r}(GM={u.get('is_gm')})" for u in users
            ) or "<none>"
            logger.error(
                "STARTUP ABORTED: no GM-logged-in Foundry browser is connected to "
                "the relay. World setup commands (create_scene/activate_scene/"
                "create_actor) require a Gamemaster session. "
                "Currently connected Foundry clients: %s. "
                "Open a browser tab, log in as your Gamemaster user, then restart "
                "Python.",
                user_str,
            )
            return
        logger.info(
            "relay census: foundry_clients=%d (gm=%d) — proceeding with startup",
            foundry_count, gm_count,
        )

    commands: list[GameCommand] = [
        # Idempotent: the JS create_scene returns the existing scene if a
        # scene with this name already exists. Activate then resolves the
        # same name (id-or-name lookup) on the next step.
        # Auto-fit width/height to encompass every authored anchor + zone
        # so the scene isn't a tiny island in a sea of grey grid (or,
        # conversely, a vast empty plain hiding the action in one corner).
        _build_create_scene(container, scene_id),
        ActivateSceneCommand(scene_id=scene_id),
    ]

    # Active PC + AI companions: create-or-update actors and spawn
    # tokens at the chosen anchor. Shared with the travel pipeline so
    # behaviour stays in lock-step.
    commands.extend(
        build_party_spawn_commands(container, pack, scene_id, pc_id=pc_id)
    )

    # Project the campaign-pack anchors for this scene as Foundry note
    # pins. Without this, ``move_actor_to "valley overlook"`` (or any
    # named landmark) cannot resolve via Foundry's ``findTargetOnScene``
    # — the available targets list contains only PC/NPC tokens. The
    # JS-side ``createNote`` is idempotent on label, so re-running on
    # restart re-uses the existing pin instead of duplicating it.
    for note_cmd in _anchor_pin_commands(container, scene_id):
        commands.append(note_cmd)

    try:
        outcome = executor.execute(commands, atomic=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("startup dispatch failed: %s", exc)
        return

    if not outcome.ok:
        failures = [r for r in outcome.results if not r.ok]
        logger.warning(
            "startup sequence had %d failure(s); state may be incomplete",
            len(failures),
        )
        for r in failures:
            logger.warning(
                "  failed: type=%s error=%s",
                getattr(r, "command_type", None) or getattr(r, "type", None),
                getattr(r, "error", None) or getattr(r, "response", None),
            )
    else:
        logger.info(
            "startup: scene=%s pc=%s spawned (%d commands ok)",
            scene_id, pc_id, len(outcome.results),
        )


def _load_character_sheet(pack: CampaignPack, pc_id: str) -> dict | None:
    candidates = [
        pack.state.characters / f"{pc_id}.json",
        pack.paths.characters_seed / f"{pc_id}.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                sheet = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("character sheet %s unreadable: %s", path, exc)
                return None
            return _migrate_spell_block(sheet)
    return None


def _migrate_spell_block(sheet: dict | None) -> dict | None:
    """Normalise a legacy v1 spells block to the v2 known/prepared shape.

    Sheets written before the casting-style split had ``known == prepared``
    and no ``casting_style`` marker. When the corresponding archetype is
    now ``"prepared"``, we copy the (effectively immutable v1) list into
    ``spellbook`` so :func:`character_spells.prepare_spells` can narrow
    it later. ``"known"`` archetypes get the redundant ``prepared`` list
    cleared. Non-casters and already-migrated sheets are untouched.
    """
    if not isinstance(sheet, dict):
        return sheet
    spells = sheet.get("spells")
    if not isinstance(spells, dict) or spells.get("casting_style"):
        return sheet
    # Resolve the archetype's casting style by looking up the class label.
    from ai_dm.app.character_wizard import ARCHETYPES  # local import to avoid cycle

    class_label = str(sheet.get("class") or "").strip().lower()
    arch = next(
        (a for a in ARCHETYPES.values() if a.label.lower() == class_label),
        None,
    )
    style = getattr(arch, "casting_style", None) if arch else None
    if not style:
        return sheet
    known = list(spells.get("known") or [])
    prepared = list(spells.get("prepared") or [])
    new_spells = dict(spells)
    if style == "prepared":
        # v1 stored picks in both ``known`` and ``prepared``; treat them
        # as the spellbook and keep the prepared list (capped daily).
        spellbook = known or prepared
        new_spells["spellbook"] = spellbook
        new_spells["prepared"] = list(spellbook)
        new_spells["known"] = []
    elif style == "known":
        new_spells["known"] = known or prepared
        new_spells["prepared"] = []
    new_spells["casting_style"] = style
    out = dict(sheet)
    out["spells"] = new_spells
    return out


# --------------------------------------------------------------------- #
# Reset hook + anchor pins
# --------------------------------------------------------------------- #


_RESET_FLAG_REL = Path("data/cache/clear_chat_on_next_start")
_RESET_FOUNDRY_FLAG_REL = Path("data/cache/reset_foundry_on_next_start")


def _publish_initial_scene_entered(pack: CampaignPack, container: Container) -> None:
    """Fire ``scene.entered`` for the manifest's start scene exactly once.

    The planner subscribes to its own ``enter_scene()`` publishes; nobody
    else was previously calling it, so triggers like ``start_intro`` and
    the road random-encounter table never had a chance to react to the
    initial scene activation.
    """
    start = pack.manifest.start or {}
    scene_id = start.get("scene")
    if not scene_id:
        return
    planner = getattr(container, "story_planner", None)
    if planner is not None:
        try:
            planner.enter_scene(scene_id)
            logger.info("planner.enter_scene(%s) published scene.entered", scene_id)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("planner.enter_scene failed: %s", exc)
    if container.event_bus is not None:
        try:
            container.event_bus.publish(
                "scene.entered", {"scene_id": scene_id, "from": None},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("scene.entered publish failed: %s", exc)


def _maybe_clear_chat(container: Container) -> None:
    """If ``scripts/reset_state.sh`` left the sentinel file, send the GM
    Foundry client a ``clear_chat`` event then delete the sentinel so
    the next launch starts the new conversation cleanly.
    """
    flag = _RESET_FLAG_REL
    if not flag.exists():
        return
    if container.client is not None:
        try:
            container.client.send_event(
                "clear_chat", {}, event_id="reset_clear_chat",
            )
            logger.info("sent clear_chat to Foundry (reset_state.sh sentinel)")
        except Exception as exc:  # noqa: BLE001
            logger.warning("clear_chat send failed: %s", exc)
    try:
        flag.unlink()
    except Exception:  # noqa: BLE001
        pass


def _maybe_reset_foundry_state(pack: CampaignPack, container: Container) -> None:
    """If ``scripts/reset_state.sh`` left the reset-foundry sentinel,
    compute the set of pack-owned scenes / actors / journals and tell
    the GM Foundry client to delete them. The new world-setup batch
    that runs immediately after this will then re-create everything
    from scratch — so a "reset" is a true clean slate, not just a
    Python-side wipe.

    Names are derived deterministically from the pack so we never
    delete GM-authored content that the AI DM didn't put there.
    """
    flag = _RESET_FOUNDRY_FLAG_REL
    if not flag.exists():
        return
    if container.client is None:
        logger.info("reset_foundry_state: no client; deleting sentinel only")
        try: flag.unlink()
        except Exception: pass
        return

    scene_names: set[str] = set()
    actor_names: set[str] = set()
    journal_names: set[str] = {"AI DM Anchors"}

    # Scenes: every chapter scene id + name across every chapter, plus
    # the manifest's start scene if absent.
    try:
        for chap in (container.chapter_service.all() if container.chapter_service else []):
            for sc in chap.scenes:
                if sc.id: scene_names.add(sc.id)
                if sc.name: scene_names.add(sc.name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reset_foundry_state: chapter scene lookup failed: %s", exc)
    start_scene = (pack.manifest.start or {}).get("scene")
    if start_scene:
        scene_names.add(start_scene)

    # Actors: PC + every party member, by id and by display name.
    pc_id = (pack.manifest.start or {}).get("player_character")
    if pc_id:
        actor_names.add(pc_id)
        sheet = _load_character_sheet(pack, pc_id)
        if sheet and sheet.get("name"):
            actor_names.add(sheet["name"])
    for member in _party_members(pack):
        cid = member.get("id")
        if cid:
            actor_names.add(cid)
        sheet = _load_character_sheet(pack, cid) if cid else None
        nm = (sheet or {}).get("name") or member.get("name")
        if nm:
            actor_names.add(nm)

    payload = {
        "scene_names": sorted(scene_names),
        "actor_names": sorted(actor_names),
        "journal_names": sorted(journal_names),
        "delete_all_tokens_in_listed_scenes": True,
    }
    try:
        container.client.send_event(
            "reset_foundry_state", payload,
            event_id=f"reset_foundry:{pack.slug}",
        )
        logger.info(
            "reset_foundry_state: requested deletion of %d scene(s), "
            "%d actor(s), %d journal(s)",
            len(payload["scene_names"]),
            len(payload["actor_names"]),
            len(payload["journal_names"]),
        )
        # Give the GM client a beat to process the deletions before the
        # world-setup batch tries to recreate everything; without this
        # create_scene races a still-pending scene delete and the new
        # scene gets removed.
        import time as _t
        _t.sleep(2.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reset_foundry_state send failed: %s", exc)
    try:
        flag.unlink()
    except Exception:  # noqa: BLE001
        pass


def _anchor_pin_commands(container, scene_id: str) -> list[CreateNoteCommand]:
    """Backwards-compatible shim around :func:`scene_setup.build_anchor_pin_commands`."""
    loc = getattr(container, "location_service", None)
    return _scene_anchor_pin_commands(loc, scene_id)



# --------------------------------------------------------------------- #
# Scene sizing + spawn placement
# --------------------------------------------------------------------- #


_MIN_SCENE_DIM = 1500   # pixels
_DEFAULT_SCENE_DIM = 4000
_SCENE_PAD = 400        # padding around the bounding box of anchors/zones
_GRID_SIZE = 100


def _scene_bounds(container, scene_id: str) -> tuple[int, int] | None:
    """Backwards-compatible shim. See :mod:`ai_dm.app.scene_setup`."""
    from ai_dm.app.scene_setup import _scene_bounds as _impl
    return _impl(getattr(container, "location_service", None), scene_id)


def _build_create_scene(container, scene_id: str) -> CreateSceneCommand:
    """Backwards-compatible shim around :func:`scene_setup.build_create_scene`."""
    return _scene_build_create_scene(getattr(container, "location_service", None), scene_id)


def _spawn_position(container, pack: CampaignPack, scene_id: str) -> tuple[int, int]:
    """Pick a sensible (x, y) for the party's initial spawn.

    Resolution order:
      1. ``manifest.start.spawn_anchor`` → matched against pack anchors
      2. anchor tagged ``entrance`` or ``start``
      3. first authored anchor
      4. centre of the scene's first zone (rect / polygon centroid)
      5. fallback (200, 200) so tokens aren't stacked at exactly (0, 0)
    """
    loc = getattr(container, "location_service", None)
    scene = loc.get_scene(scene_id) if (loc and scene_id) else None
    anchors = list(scene.anchors) if scene else []

    desired = (pack.manifest.start or {}).get("spawn_anchor")
    if desired and anchors:
        norm = str(desired).strip().lower()
        for a in anchors:
            if a.id == desired or a.name.lower() == norm:
                return (int(a.x), int(a.y))

    for tag in ("entrance", "start"):
        for a in anchors:
            if tag in (a.tags or []):
                return (int(a.x), int(a.y))

    if anchors:
        return (int(anchors[0].x), int(anchors[0].y))

    if scene and scene.zones:
        z = scene.zones[0]
        cx, cy = z.centroid()
        return (int(cx), int(cy))

    return (200, 200)




# --------------------------------------------------------------------- #
# Party spawn helper (shared by startup + travel)
# --------------------------------------------------------------------- #


def _registry_actor_known(registry, alias_or_id: str):
    """Return the registry entry for ``alias_or_id`` or None.

    Unlike ``registry.get("actor", x)`` (which only matches the Foundry
    document id), this walks the alias index so canonical pack ids like
    ``pc_human`` count as "known" once they've been registered as an
    alias on a previous startup. Without this, the bootstrap helper
    re-issued ``create_actor`` on every party move, which both spammed
    Foundry and prevented the canonical id from ever ending up in the
    registry.
    """
    if registry is None or not alias_or_id:
        return None
    try:
        foundry_id = registry.resolve("actor", alias_or_id)
    except Exception:  # noqa: BLE001 — RegistryMissError + defensive
        return None
    return registry.get("actor", foundry_id)


def build_party_spawn_commands(
    container,
    pack: CampaignPack,
    scene_id: str,
    *,
    pc_id: str | None = None,
) -> list[GameCommand]:
    """Build the create-actor / update-actor / spawn-token commands
    needed to place the active PC plus every AI companion onto
    ``scene_id``.

    The Foundry-side ``spawnToken`` is idempotent per (scene, actor),
    so re-issuing these on every travel reuses the existing token
    instead of duplicating it. ``create_actor`` is skipped when the
    registry already knows the PC; ``update_actor`` is issued instead
    so HP/level changes propagate.

    Used by:
      * ``_apply_hardcoded_start`` — initial scene at startup.
      * ``IntentRouter._dispatch_travel`` — every cross-scene travel,
        so the player keeps a controlled token (and vision) on the
        new scene instead of arriving as a disembodied camera.
    """
    if pack is None or not scene_id:
        return []

    commands: list[GameCommand] = []
    pc_id = pc_id or _resolve_active_pc_id(pack)

    spawn_x, spawn_y = _spawn_position(container, pack, scene_id)

    # Active PC -------------------------------------------------------- #
    pc_sheet = _load_character_sheet(pack, pc_id) if pc_id else None
    pc_name = (pc_sheet.get("name") if pc_sheet else None) or (pc_id or "PC")
    spawn_actor_ref: str = pc_id or pc_name
    if pc_id and _registry_actor_known(container.registry, pc_id) is None:
        payload = dnd5e_actor_payload(pc_sheet)
        commands.append(
            CreateActorCommand(
                name=pc_name,
                actor_type="character",
                system=payload["system"],
                items=payload["items"],
                # Register the canonical pack id as an alias so a later
                # ``move_actor_to(actor_id="pc_human")`` resolves via the
                # validator instead of being passed through to JS where
                # ``resolveActor("pc_human")`` would return null and the
                # move would fail with "no token for actor pc_human".
                aliases=[pc_id],
            )
        )
        spawn_actor_ref = pc_name
    elif pc_id:
        patch = _dnd5e_patch_from_sheet(pc_sheet)
        if patch:
            commands.append(UpdateActorCommand(actor_id=pc_id, patch=patch))

    if pc_id:
        commands.append(
            SpawnTokenCommand(
                scene_id=scene_id,
                actor_id=spawn_actor_ref,
                x=spawn_x,
                y=spawn_y,
                name=pc_name,
            )
        )

    # Companions ------------------------------------------------------- #
    party = _party_members(pack)
    offset_step = 100
    other_idx = 0
    for member in party:
        cid = member.get("id")
        if not cid or cid == pc_id:
            continue
        sheet = _load_character_sheet(pack, cid)
        member_name = (sheet.get("name") if sheet else None) or member.get("name") or cid
        if _registry_actor_known(container.registry, cid) is None:
            member_payload = dnd5e_actor_payload(sheet)
            commands.append(
                CreateActorCommand(
                    name=member_name,
                    actor_type="character",
                    system=member_payload["system"],
                    items=member_payload["items"],
                    # See PC branch above — ensures the canonical pack id
                    # ("companion_witch", etc.) becomes a registry alias
                    # so party-scope moves can resolve each NPC.
                    aliases=[cid],
                )
            )
            actor_ref: str = member_name
        else:
            actor_ref = cid
            patch = _dnd5e_patch_from_sheet(sheet)
            if patch:
                commands.append(UpdateActorCommand(actor_id=cid, patch=patch))
        other_idx += 1
        sign = -1 if other_idx % 2 == 0 else 1
        offset = sign * offset_step * ((other_idx + 1) // 2)
        commands.append(
            SpawnTokenCommand(
                scene_id=scene_id,
                actor_id=actor_ref,
                x=max(0, spawn_x + offset),
                y=spawn_y,
                name=member_name,
            )
        )

    return commands


def _resolve_active_pc_id(pack: CampaignPack) -> str | None:
    """Best-effort active-PC id: manifest's ``start.pc`` then first party member."""
    pc_id = (pack.manifest.start or {}).get("pc")
    if pc_id:
        return pc_id
    members = _party_members(pack)
    return members[0]["id"] if members else None


def _build_party_brief(pack: CampaignPack, *, exclude_id: str | None) -> list[dict]:
    """Compact per-companion summaries for the narrator's prompt context.

    Reads the manifest's ``start.party`` declaration and pairs each
    member with its live (or seed) character sheet. Excludes the active
    player character so it is not duplicated alongside ``character``.
    """
    members = _party_members(pack)
    brief: list[dict] = []
    for m in members:
        cid = m.get("id")
        if not cid or cid == exclude_id:
            continue
        sheet = _load_character_sheet(pack, cid) or {}
        hp = sheet.get("hp") or {}
        entry = {
            "id": cid,
            "name": sheet.get("name") or m.get("name") or cid.replace("_", " ").title(),
            "controller": sheet.get("controller") or m.get("controller") or "ai",
        }
        for key in ("class", "level", "background", "personality"):
            val = sheet.get(key)
            if val:
                entry[key] = val
        if isinstance(hp, dict) and hp:
            entry["hp"] = {
                "current": hp.get("current"),
                "max": hp.get("max"),
            }
        if m.get("role"):
            entry["role"] = m["role"]
        brief.append(entry)
    return brief


# --------------------------------------------------------------------- #
# dnd5e system mapping
# --------------------------------------------------------------------- #


_DND5E_ABILITY_KEYS = ("str", "dex", "con", "int", "wis", "cha")


def _dnd5e_patch_from_sheet(sheet: dict | None) -> dict:
    """Translate a wizard / seed character sheet into a dnd5e ``system`` patch.

    Returns a dict of dotted-path keys (e.g. ``system.attributes.hp.max``)
    that the Foundry bridge expands into nested objects before passing to
    ``Actor.create`` / ``actor.update``. Empty dict if ``sheet`` is None.
    """
    if not sheet:
        return {}
    patch: dict = {}

    abilities = sheet.get("abilities") or {}
    for key in _DND5E_ABILITY_KEYS:
        val = abilities.get(key)
        if isinstance(val, int):
            patch[f"system.abilities.{key}.value"] = val

    hp = sheet.get("hp") or {}
    if isinstance(hp, dict):
        if isinstance(hp.get("max"), int):
            patch["system.attributes.hp.max"] = hp["max"]
        if isinstance(hp.get("current"), int):
            patch["system.attributes.hp.value"] = hp["current"]
        if isinstance(hp.get("temp"), int):
            patch["system.attributes.hp.temp"] = hp["temp"]
    elif isinstance(hp, int):
        patch["system.attributes.hp.max"] = hp
        patch["system.attributes.hp.value"] = hp

    if isinstance(sheet.get("ac"), int):
        # dnd5e accepts a flat AC override; this avoids needing armour items.
        patch["system.attributes.ac.flat"] = sheet["ac"]
        patch["system.attributes.ac.calc"] = "flat"

    if isinstance(sheet.get("speed"), int):
        patch["system.attributes.movement.walk"] = sheet["speed"]

    if isinstance(sheet.get("proficiency_bonus"), int):
        patch["system.attributes.prof"] = sheet["proficiency_bonus"]

    if isinstance(sheet.get("level"), int):
        # Stored on the actor as a fallback; class items would normally drive
        # this in dnd5e, but we don't auto-create class items yet.
        patch["system.details.level"] = sheet["level"]

    if isinstance(sheet.get("background"), str) and sheet["background"]:
        patch["system.details.background"] = sheet["background"]

    if isinstance(sheet.get("alignment"), str) and sheet["alignment"]:
        patch["system.details.alignment"] = sheet["alignment"]

    bio_parts: list[str] = []
    for field in ("appearance", "personality", "notes"):
        val = sheet.get(field)
        if isinstance(val, str) and val.strip():
            bio_parts.append(f"<p><strong>{field.title()}:</strong> {val}</p>")
    if bio_parts:
        patch["system.details.biography.value"] = "".join(bio_parts)

    # Currency (gp / sp / cp / ep / pp).
    currency = sheet.get("currency") or {}
    if isinstance(currency, dict):
        for denom in ("pp", "gp", "ep", "sp", "cp"):
            val = currency.get(denom)
            if isinstance(val, int):
                patch[f"system.currency.{denom}"] = val

    return patch


# --------------------------------------------------------------------- #
# dnd5e embedded-Item payload (weapons, armour, packs, spells, feats)
# --------------------------------------------------------------------- #


def _dnd5e_item_doc(entry: dict) -> dict | None:
    """Convert a sheet ``inventory[i]`` entry into a Foundry dnd5e Item document."""
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name") or entry.get("id") or "").strip()
    if not name:
        return None
    kind = str(entry.get("type") or "gear").lower()
    qty = int(entry.get("qty", 1) or 1)
    weight = entry.get("weight")
    value_gp = entry.get("value_gp")
    description = entry.get("description") or ""

    base: dict = {
        "name": name,
        "system": {
            "quantity": qty,
            "description": {"value": str(description)},
        },
    }
    if isinstance(weight, (int, float)):
        base["system"]["weight"] = float(weight)
    if isinstance(value_gp, (int, float)):
        base["system"]["price"] = {"value": float(value_gp), "denomination": "gp"}

    if kind == "weapon":
        base["type"] = "weapon"
        sysd = base["system"]
        sysd["weaponType"] = entry.get("weapon_type", "simpleM")
        sysd["equipped"] = bool(entry.get("equipped", False))
        damage = entry.get("damage") or {}
        if isinstance(damage, dict):
            sysd["damage"] = {
                "parts": list(damage.get("parts") or []),
                "versatile": damage.get("versatile", ""),
            }
        rng = entry.get("range") or {}
        if isinstance(rng, dict):
            sysd["range"] = {
                "value": rng.get("value"),
                "long": rng.get("long"),
                "units": rng.get("units", "ft"),
            }
        props = entry.get("properties") or []
        if isinstance(props, list):
            sysd["properties"] = {p: True for p in props}
        return base

    if kind == "armor":
        base["type"] = "equipment"
        sysd = base["system"]
        armor = entry.get("armor") or {}
        sysd["armor"] = {
            "type": armor.get("type", "light"),
            "value": armor.get("value", 10),
            "dex": armor.get("dex"),
        }
        sysd["equipped"] = bool(entry.get("equipped", False))
        return base

    if kind == "shield":
        base["type"] = "equipment"
        sysd = base["system"]
        sysd["armor"] = {
            "type": "shield",
            "value": int(entry.get("ac_bonus", 2) or 2),
            "dex": None,
        }
        sysd["equipped"] = bool(entry.get("equipped", False))
        return base

    if kind == "consumable":
        base["type"] = "consumable"
        return base

    if kind == "tool":
        base["type"] = "tool"
        return base

    if kind == "ammo":
        base["type"] = "consumable"
        base["system"]["consumableType"] = "ammo"
        return base

    # pack / gear / unknown → loot
    base["type"] = "loot"
    return base


def _dnd5e_spell_doc(entry: dict) -> dict | None:
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name") or entry.get("id") or "").strip()
    if not name:
        return None
    components = entry.get("components") or {}
    return {
        "name": name,
        "type": "spell",
        "system": {
            "level": int(entry.get("level", 0) or 0),
            "school": entry.get("school", ""),
            "components": {
                "vocal": bool(components.get("v")),
                "somatic": bool(components.get("s")),
                "material": bool(components.get("m")),
            },
            "preparation": {
                "mode": "prepared",
                "prepared": bool(entry.get("prepared", True)),
            },
            "activation": {
                "type": str(entry.get("casting_time") or "action"),
                "cost": 1,
            },
            "duration": {"value": None, "units": "inst"},
            "range": {"value": None, "long": None, "units": ""},
            "description": {"value": str(entry.get("description") or "")},
        },
    }


def _dnd5e_feature_doc(entry: dict) -> dict | None:
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name") or entry.get("id") or "").strip()
    if not name:
        return None
    activation = entry.get("activation") or {"type": "passive", "cost": 0}
    sysd: dict = {
        "activation": {
            "type": activation.get("type", "passive"),
            "cost": activation.get("cost", 0),
        },
        "description": {"value": str(entry.get("description") or "")},
        "source": {"book": "AI-DM", "custom": str(entry.get("source") or "")},
    }
    uses = entry.get("uses")
    if isinstance(uses, dict):
        sysd["uses"] = {
            "value": uses.get("value"),
            "max": uses.get("max"),
            "per": uses.get("per"),
        }
    return {"name": name, "type": "feat", "system": sysd}


def _dnd5e_items_from_sheet(sheet: dict | None) -> list[dict]:
    """Translate a sheet's inventory + spells + abilities_features into Foundry Item docs."""
    if not sheet:
        return []
    out: list[dict] = []
    for entry in sheet.get("inventory") or []:
        doc = _dnd5e_item_doc(entry)
        if doc:
            out.append(doc)
    spells_block = sheet.get("spells") or {}
    if isinstance(spells_block, dict):
        seen_spell_names: set[str] = set()
        for bucket in ("cantrips_known", "spellbook", "prepared", "known"):
            for spell in spells_block.get(bucket) or []:
                doc = _dnd5e_spell_doc(spell)
                if not doc:
                    continue
                key = doc["name"].lower()
                if key in seen_spell_names:
                    continue
                seen_spell_names.add(key)
                out.append(doc)
    for feat in sheet.get("abilities_features") or []:
        doc = _dnd5e_feature_doc(feat)
        if doc:
            out.append(doc)
    return out


def dnd5e_actor_payload(sheet: dict | None) -> dict:
    """Build the full create-actor payload (system patch + embedded items)."""
    return {
        "system": _dnd5e_patch_from_sheet(sheet),
        "items": _dnd5e_items_from_sheet(sheet),
    }


