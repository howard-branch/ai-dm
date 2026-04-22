"""Application bootstrap.

Loads ``config/settings.yaml``, resolves the active campaign pack, and
wires up the :class:`Container` and :class:`Director`.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

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
    if container.registry.get("actor", pc_id) is None:
        commands.append(CreateActorCommand(name=pc_name, actor_type="character"))

    # Spawn at scene origin — anchor resolution can come later.
    commands.append(
        SpawnTokenCommand(
            scene_id=scene_id,
            actor_id=pc_id,
            x=0,
            y=0,
            name=pc_name,
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

