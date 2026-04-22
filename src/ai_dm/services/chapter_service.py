"""Loads chapter assets from a campaign pack's ``chapters/`` directory."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ai_dm.ai.arc_schemas import Beat, Chapter, Scene

logger = logging.getLogger("ai_dm.services.chapter")


class ChapterService:
    def __init__(self, base: Path | None = None) -> None:
        # ``base`` is normally injected from ``CampaignPack.paths.chapters``.
        # The ``None`` fallback yields an empty service (handy for tests).
        self.base = Path(base) if base is not None else Path()
        self._chapters: dict[str, Chapter] = {}
        self._load()

    def _load(self) -> None:
        if not self.base.exists():
            return
        for d in sorted(self.base.iterdir()):
            if not d.is_dir():
                continue
            try:
                self._chapters[d.name] = self._load_one(d)
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to load chapter %s: %s", d.name, exc)

    def _load_one(self, directory: Path) -> Chapter:
        chap_id = directory.name
        title = chap_id.replace("_", " ").title()
        summary = ""
        if (directory / "summary.md").exists():
            summary = (directory / "summary.md").read_text(encoding="utf-8")
        scenes: list[Scene] = []
        if (directory / "scenes.json").exists():
            data = json.loads((directory / "scenes.json").read_text(encoding="utf-8"))
            for s in (data or []) if isinstance(data, list) else (data.get("scenes", [])):
                beats = [Beat(**b) for b in s.get("beats", [])]
                scenes.append(
                    Scene(
                        id=s.get("id") or s.get("name", "scene"),
                        name=s.get("name", ""),
                        summary=s.get("summary", ""),
                        beats=beats,
                    )
                )
        encounters = _maybe_json(directory / "encounters.json")
        npcs = _maybe_json(directory / "npcs.json")
        return Chapter(
            id=chap_id,
            title=title,
            summary=summary,
            scenes=scenes,
            encounters=encounters if isinstance(encounters, list) else [],
            npcs=npcs if isinstance(npcs, list) else [],
        )

    # ------------------------------------------------------------------ #

    def get(self, chapter_id: str) -> Chapter | None:
        return self._chapters.get(chapter_id)

    def all(self) -> list[Chapter]:
        return list(self._chapters.values())

    def first_id(self) -> str | None:
        for chap in self._chapters.values():
            return chap.id
        return None

    def current_chapter(self) -> str:
        return self.first_id() or "chapter_01"


def _maybe_json(path: Path):
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
