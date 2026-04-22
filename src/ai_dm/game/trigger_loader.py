"""Load triggers from disk (YAML or JSON) into :class:`Trigger` instances."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ai_dm.orchestration.triggers import Trigger, trigger_from_spec

logger = logging.getLogger("ai_dm.triggers.loader")


def load_triggers(directory: Path, *, deps: dict) -> list[Trigger]:
    """Load every ``*.yaml`` / ``*.yml`` / ``*.json`` file under ``directory``."""
    if not directory.exists():
        return []
    out: list[Trigger] = []
    for p in sorted(directory.iterdir()):
        if p.is_dir():
            out.extend(load_triggers(p, deps=deps))
            continue
        try:
            data = _read(p)
        except Exception as exc:  # noqa: BLE001
            logger.warning("trigger file %s unreadable: %s", p, exc)
            continue
        for entry in _iter_specs(data):
            try:
                out.append(trigger_from_spec(entry, deps=deps))
            except Exception as exc:  # noqa: BLE001
                logger.warning("invalid trigger in %s: %s", p, exc)
    return out


def _read(path: Path):
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-not-found]
        except Exception:  # noqa: BLE001
            raise RuntimeError("PyYAML required for YAML triggers")
        return yaml.safe_load(text)
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return None


def _iter_specs(data):
    if data is None:
        return []
    if isinstance(data, dict):
        if "triggers" in data and isinstance(data["triggers"], list):
            return data["triggers"]
        return [data]
    if isinstance(data, list):
        return data
    return []

