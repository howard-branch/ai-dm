"""Pluggable campaign packs.

Layout of a campaign pack (read-only, distributable)::

    campaigns/<slug>/
        campaign.yaml            # manifest (id, name, version, overrides)
        chapters/
        locations/
        prompts/
            system_narrator.txt
            output_schema.json
        triggers/
        world/
        voices.json
        characters/seed/         # starter PCs/companions copied to state
        portraits/
        maps/
        audio/

Per-campaign mutable runtime state lives outside the pack so packs stay
read-only::

    <state_root>/<slug>/
        characters/              # live, mutated character sheets
        saves/
            backups/
        memory/
        cache/

The active pack is selected via ``config/settings.yaml``::

    campaigns:
      root: campaigns
      active: morgana          # slug under root, OR a path to a pack
      state_root: data/campaigns

A *legacy fallback* (``CampaignPack.from_legacy_layout``) wraps the old
``assets/campaign`` + ``data/saves`` layout so existing tests keep
working through the migration.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai_dm.campaign.pack")


# --------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------- #


_DEFAULT_OVERRIDES: dict[str, str] = {
    "chapters": "chapters",
    "locations": "locations",
    "prompts": "prompts",
    "triggers": "triggers",
    "world": "world",
    "voices": "voices.json",
    "characters_seed": "characters/seed",
    "portraits": "portraits",
    "maps": "maps",
    "audio": "audio",
    "system_narrator": "prompts/system_narrator.txt",
    "output_schema": "prompts/output_schema.json",
}


@dataclass(frozen=True)
class CampaignManifest:
    """Parsed ``campaign.yaml``."""

    id: str
    name: str = ""
    version: str = "0.0.0"
    entrypoints: dict[str, Any] = field(default_factory=dict)
    overrides: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "CampaignManifest":
        data: dict[str, Any] = {}
        if path.exists():
            text = path.read_text(encoding="utf-8")
            try:
                import yaml  # type: ignore[import-not-found]

                data = yaml.safe_load(text) or {}
            except Exception:  # noqa: BLE001
                # Fall back to JSON if PyYAML not available or file is JSON.
                import json

                try:
                    data = json.loads(text)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("manifest %s unreadable: %s", path, exc)
                    data = {}
        return cls(
            id=str(data.get("id") or path.parent.name),
            name=str(data.get("name") or ""),
            version=str(data.get("version") or "0.0.0"),
            entrypoints=dict(data.get("entrypoints") or {}),
            overrides=dict(data.get("overrides") or {}),
        )

    @classmethod
    def synthetic(cls, slug: str) -> "CampaignManifest":
        return cls(id=slug, name=slug, version="legacy")


# --------------------------------------------------------------------- #
# Paths (read-only, inside the pack)
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class CampaignPaths:
    """All read-only paths inside a campaign pack."""

    root: Path
    overrides: dict[str, str] = field(default_factory=dict)

    def _p(self, key: str) -> Path:
        rel = self.overrides.get(key) or _DEFAULT_OVERRIDES[key]
        return self.root / rel

    @property
    def chapters(self) -> Path: return self._p("chapters")
    @property
    def locations(self) -> Path: return self._p("locations")
    @property
    def prompts(self) -> Path: return self._p("prompts")
    @property
    def triggers(self) -> Path: return self._p("triggers")
    @property
    def world(self) -> Path: return self._p("world")
    @property
    def voices(self) -> Path: return self._p("voices")
    @property
    def characters_seed(self) -> Path: return self._p("characters_seed")
    @property
    def portraits(self) -> Path: return self._p("portraits")
    @property
    def maps(self) -> Path: return self._p("maps")
    @property
    def audio(self) -> Path: return self._p("audio")
    @property
    def system_narrator(self) -> Path: return self._p("system_narrator")
    @property
    def output_schema(self) -> Path: return self._p("output_schema")


# --------------------------------------------------------------------- #
# State (writable, outside the pack)
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class CampaignState:
    """All writable per-campaign paths under ``<state_root>/<slug>/``."""

    root: Path

    @property
    def characters(self) -> Path: return self.root / "characters"
    @property
    def saves(self) -> Path: return self.root / "saves"
    @property
    def backups(self) -> Path: return self.saves / "backups"
    @property
    def memory(self) -> Path: return self.root / "memory"
    @property
    def cache(self) -> Path: return self.root / "cache"

    def ensure(self) -> None:
        for p in (self.characters, self.saves, self.backups, self.memory, self.cache):
            p.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------- #
# Pack
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class CampaignPack:
    """A loaded campaign pack with read paths and writable state paths."""

    root: Path
    manifest: CampaignManifest
    paths: CampaignPaths
    state: CampaignState

    @property
    def slug(self) -> str:
        return self.manifest.id

    # ---- factories ------------------------------------------------- #

    @classmethod
    def load(
        cls,
        root: Path,
        *,
        state_root: Path,
        manifest_filename: str = "campaign.yaml",
    ) -> "CampaignPack":
        root = Path(root)
        manifest = CampaignManifest.load(root / manifest_filename)
        paths = CampaignPaths(root=root, overrides=manifest.overrides)
        state = CampaignState(root=Path(state_root) / manifest.id)
        return cls(root=root, manifest=manifest, paths=paths, state=state)

    @classmethod
    def from_legacy_layout(
        cls,
        campaign_assets: Path,
        saves_dir: Path,
        *,
        slug: str = "default",
        characters_seed: Path | None = None,
    ) -> "CampaignPack":
        """Wrap the pre-pack ``assets/campaign`` + ``data/saves`` layout.

        ``saves_dir`` is treated as the *already-resolved* per-campaign
        saves directory (no slug nesting). We achieve that by setting
        ``state.root = saves_dir.parent`` so ``state.saves`` returns
        ``saves_dir`` unchanged. Live characters land in
        ``saves_dir.parent / "characters"`` by the same trick.
        """
        root = Path(campaign_assets)
        manifest = CampaignManifest.synthetic(slug)
        seed = characters_seed
        if seed is None:
            # Default legacy seed location: assets/characters/ alongside
            # assets/campaign/.
            seed = root.parent / "characters" if root.parent != Path() else Path("assets/characters")
        legacy_overrides = {"characters_seed": str(seed)}
        paths = CampaignPaths(root=root, overrides=legacy_overrides)
        # state.root is chosen so that state.saves == saves_dir exactly,
        # preserving the flat layout the legacy code expected.
        state = CampaignState(root=Path(saves_dir).parent)
        return cls(root=root, manifest=manifest, paths=paths, state=state)


# --------------------------------------------------------------------- #
# Resolution + seeding helpers
# --------------------------------------------------------------------- #


def resolve_pack(
    selector: str,
    *,
    campaigns_root: Path,
    state_root: Path,
) -> CampaignPack:
    """Resolve ``selector`` (slug or path) into a loaded :class:`CampaignPack`.

    * If ``selector`` is an existing path (absolute or relative to CWD),
      it is treated as a pack root.
    * Otherwise it is looked up under ``campaigns_root / selector``.
    """
    candidate = Path(selector)
    if not candidate.is_absolute():
        # Try as a path first, then as a slug.
        if candidate.exists() and candidate.is_dir():
            root = candidate
        else:
            root = Path(campaigns_root) / selector
    else:
        root = candidate
    if not root.exists():
        raise FileNotFoundError(f"campaign pack not found: {root}")
    return CampaignPack.load(root, state_root=Path(state_root))


def seed_characters(pack: CampaignPack, *, overwrite: bool = False) -> list[Path]:
    """Copy ``pack.paths.characters_seed/*`` into ``pack.state.characters``.

    Idempotent: existing files are left untouched unless ``overwrite``.
    Returns the list of paths written.
    """
    src = pack.paths.characters_seed
    dst = pack.state.characters
    if not src.exists():
        return []
    dst.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for entry in src.rglob("*"):
        if entry.is_dir():
            continue
        rel = entry.relative_to(src)
        target = dst / rel
        if target.exists() and not overwrite:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry, target)
        written.append(target)
    return written

