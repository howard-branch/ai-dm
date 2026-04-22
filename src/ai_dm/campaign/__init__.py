"""Campaign-pack abstraction.

A *campaign pack* is a self-contained directory of campaign-specific
content (chapters, locations, prompts, triggers, world lore, voices,
seed character sheets, maps, portraits, audio). Packs are read-only
and distributable; per-campaign mutable state (live characters, saves,
backups, memory) lives outside the pack under a configurable
``state_root``, keyed by the campaign's slug.

Switching campaigns is a single config change: ``campaigns.active``.
"""
from ai_dm.campaign.pack import (
    CampaignManifest,
    CampaignPack,
    CampaignPaths,
    CampaignState,
    resolve_pack,
    seed_characters,
)

__all__ = [
    "CampaignManifest",
    "CampaignPack",
    "CampaignPaths",
    "CampaignState",
    "resolve_pack",
    "seed_characters",
]

