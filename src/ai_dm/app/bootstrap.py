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
from ai_dm.app.runtime import Runtime
from ai_dm.app.settings import Settings
from ai_dm.campaign.pack import CampaignPack, resolve_pack, seed_characters
from ai_dm.game.state_store import StateStore
from ai_dm.models.commands import (
    ActivateSceneCommand,
    CreateActorCommand,
    CreateSceneCommand,
    SpawnTokenCommand,
    UpdateActorCommand,
)
from ai_dm.orchestration.director import Director

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
    _maybe_run_character_wizard(pack)
    _seed_ai_companions(pack)
    audio_enabled = _env_bool("AI_DM_AUDIO", default=True)
    edge_voice = os.environ.get("TTS_VOICE") or "en-GB-SoniaNeural"
    container = Container.build(
        ContainerConfig(pack=pack, audio_enabled=audio_enabled, edge_voice=edge_voice)
    )

    # Inject the active player character into the prompt context so the
    # narrator knows who's speaking. Pulled from the manifest's
    # ``start.player_character`` and the live character sheet (seeded
    # on first run by the container build).
    pc_id = (pack.manifest.start or {}).get("player_character")
    pc_sheet = _load_character_sheet(pack, pc_id) if pc_id else None
    if pc_sheet and container.prompt_context is not None:
        container.prompt_context.character = pc_sheet

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
        event_bus=container.event_bus,  # publishes narrator.output_ready
    )
    _apply_hardcoded_start(pack, container)
    return Runtime(director=director, container=container)


# --------------------------------------------------------------------- #
# Character wizard gate
# --------------------------------------------------------------------- #


def _maybe_run_character_wizard(pack: CampaignPack) -> None:
    """Run the guided character creator if the active pack has no PC sheet.

    Forced on with ``AI_DM_NEW_CHARACTER=1``. Skipped if the manifest has
    no ``start.player_character``, if a live sheet exists, or (when not
    forced) if a seed sheet exists.
    """
    pc_id = (pack.manifest.start or {}).get("player_character")
    if not pc_id:
        return
    forced = _env_bool("AI_DM_NEW_CHARACTER", default=False)
    if not forced and not needs_wizard(pack, pc_id):
        return
    try:
        sheet = run_wizard(pc_id)
    except (EOFError, KeyboardInterrupt):
        logger.warning("character wizard cancelled; continuing with existing state")
        return
    try:
        path = write_sheet(pack, pc_id, sheet)
        logger.info("character wizard wrote sheet: %s", path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("character wizard write failed: %s", exc)


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
            sheet = build_sheet(cid, name, archetype, None, background)
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

    pc_sheet = _load_character_sheet(pack, pc_id)
    pc_name = (pc_sheet.get("name") if pc_sheet else None) or pc_id

    # 2. Push the start sequence to Foundry: activate scene → create
    #    actor (idempotent if already registered) → spawn token.
    executor = container.executor
    if executor is None:
        logger.info("no executor available; skipping Foundry startup writes")
        return

    commands = [
        # Idempotent: the JS create_scene returns the existing scene if a
        # scene with this name already exists. Activate then resolves the
        # same name (id-or-name lookup) on the next step.
        CreateSceneCommand(name=scene_id),
        ActivateSceneCommand(scene_id=scene_id),
    ]

    # Skip create_actor if the registry already knows this PC; otherwise
    # attempt to create one. The BatchExecutor will register the result.
    #
    # When we create/discover the actor by display name, token spawning must
    # use that same resolvable reference on the first startup pass. Some packs
    # (including non-Morgana ones) use a stable character-sheet id that does
    # not match the Foundry actor name.
    spawn_actor_ref = pc_id
    if container.registry.get("actor", pc_id) is None:
        commands.append(
            CreateActorCommand(
                name=pc_name,
                actor_type="character",
                system=_dnd5e_patch_from_sheet(pc_sheet),
            )
        )
        spawn_actor_ref = pc_name
    else:
        # Actor already exists in Foundry — push a sync update so any sheet
        # changes (HP, stats, level) are reflected without re-creating it.
        patch = _dnd5e_patch_from_sheet(pc_sheet)
        if patch:
            commands.append(UpdateActorCommand(actor_id=pc_id, patch=patch))

    # Spawn at scene origin — anchor resolution can come later.
    commands.append(
        SpawnTokenCommand(
            scene_id=scene_id,
            actor_id=spawn_actor_ref,
            x=0,
            y=0,
            name=pc_name,
        )
    )

    # Spawn the rest of the party (AI-controlled companions) at small
    # offsets around the PC. Idempotency on Foundry's side is the JS
    # bridge's responsibility; we issue the commands unconditionally so
    # restarts after a wipe re-place missing tokens.
    party = _party_members(pack)
    offset_step = 100  # pixel offset between adjacent party tokens
    other_idx = 0
    for member in party:
        cid = member["id"]
        if cid == pc_id:
            continue
        sheet = _load_character_sheet(pack, cid)
        member_name = (sheet.get("name") if sheet else None) or member.get("name") or cid
        if container.registry.get("actor", cid) is None:
            commands.append(
                CreateActorCommand(
                    name=member_name,
                    actor_type="character",
                    system=_dnd5e_patch_from_sheet(sheet),
                )
            )
            actor_ref: str = member_name
        else:
            actor_ref = cid
            patch = _dnd5e_patch_from_sheet(sheet)
            if patch:
                commands.append(UpdateActorCommand(actor_id=cid, patch=patch))
        other_idx += 1
        # Fan out: alternate left/right of the PC.
        sign = -1 if other_idx % 2 == 0 else 1
        offset = sign * offset_step * ((other_idx + 1) // 2)
        commands.append(
            SpawnTokenCommand(
                scene_id=scene_id,
                actor_id=actor_ref,
                x=offset,
                y=0,
                name=member_name,
            )
        )

    try:
        outcome = executor.execute(commands, atomic=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("startup dispatch failed: %s", exc)
        return

    if not outcome.ok:
        logger.warning(
            "startup sequence had %d failure(s); state may be incomplete",
            sum(1 for r in outcome.results if not r.ok),
        )
    else:
        logger.info(
            "startup: scene=%s pc=%s spawned", scene_id, pc_id,
        )


def _load_character_sheet(pack: CampaignPack, pc_id: str) -> dict | None:
    candidates = [
        pack.state.characters / f"{pc_id}.json",
        pack.paths.characters_seed / f"{pc_id}.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("character sheet %s unreadable: %s", path, exc)
                return None
    return None


# --------------------------------------------------------------------- #
# Party briefs (for the narrator's prompt context)
# --------------------------------------------------------------------- #


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

    return patch


