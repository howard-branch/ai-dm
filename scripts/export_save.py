"""Print current_session.json from the active campaign's saves dir."""
from __future__ import annotations

from pathlib import Path

from ai_dm.app.settings import Settings
from ai_dm.campaign.pack import CampaignPack, resolve_pack


def _active_pack() -> CampaignPack:
    s = Settings.load()
    if s.campaigns.active:
        return resolve_pack(
            s.campaigns.active,
            campaigns_root=s.campaigns.root,
            state_root=s.campaigns.state_root,
        )
    return CampaignPack.from_legacy_layout(
        campaign_assets=Path("assets/campaign"),
        saves_dir=Path("data/saves"),
    )


if __name__ == "__main__":
    pack = _active_pack()
    print((pack.state.saves / "current_session.json").read_text(encoding="utf-8"))
