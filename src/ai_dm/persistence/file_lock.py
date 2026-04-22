"""POSIX file lock using ``fcntl``."""
from __future__ import annotations

import os
from pathlib import Path
from typing import IO

try:
    import fcntl  # type: ignore
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover — Windows fallback
    fcntl = None  # type: ignore
    _HAS_FCNTL = False


class FileLock:
    """Exclusive file lock; safe nested no-op on platforms without fcntl."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fh: IO[bytes] | None = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "ab+")
        if _HAS_FCNTL:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if self._fh is not None:
                if _HAS_FCNTL:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                self._fh.close()
        finally:
            self._fh = None
        return False
