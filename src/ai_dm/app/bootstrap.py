"""Application bootstrap.

Loads ``config/settings.yaml``, resolves the active campaign pack, and
wires up the :class:`Container` and :class:`Director`.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ai_dm.app.container import Container, ContainerConfig
from ai_dm.app.runtime import Runtime
from ai_dm.app.settings import Settings
from ai_dm.campaign.pack import CampaignPack, resolve_pack
from ai_dm.game.state_store import StateStore
from ai_dm.orchestration.director import Director

logger = logging.getLogger("ai_dm.app.bootstrap")


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
    container = Container.build(ContainerConfig(pack=pack))
    state_store = StateStore(base=pack.state.saves)
    director = Director(
        state_store=state_store,
        command_router=container.command_router,
        narrator=container.narrator,
        prompt_context=container.prompt_context,
        npc_memory=container.npc_memory,
    )
    return Runtime(director=director, container=container)
