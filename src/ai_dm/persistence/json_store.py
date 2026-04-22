"""Atomic JSON read/write façade."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_dm.persistence.atomic_writer import atomic_write_json
from ai_dm.persistence.file_lock import FileLock


class JsonStore:
    def read(self, path: Path) -> dict:
        path = Path(path)
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, path: Path, payload: Any) -> None:
        path = Path(path)
        lock_path = path.with_suffix(path.suffix + ".lock")
        with FileLock(lock_path):
            atomic_write_json(path, payload)
