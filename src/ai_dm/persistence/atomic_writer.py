"""Atomic JSON file writer (tempfile + os.replace)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, payload: Any, *, indent: int = 2) -> None:
    """Atomically replace ``path`` with ``payload`` serialised as JSON.

    Writes to a temporary file in the same directory, ``fsync``s, and
    ``os.replace``s into place so a crash mid-write leaves the previous
    contents untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=indent, default=str, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise

