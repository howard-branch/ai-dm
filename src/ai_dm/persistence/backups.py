"""Rolling backup service for save files."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path


class BackupService:
    def __init__(self, dir: Path, *, max_backups: int = 20) -> None:
        self.dir = Path(dir)
        self.max_backups = max_backups

    def rotate(self, source: Path) -> Path | None:
        """Copy ``source`` into the backup dir with a timestamp suffix.

        Returns the new backup path, or ``None`` if ``source`` does not
        exist (nothing to back up).
        """
        source = Path(source)
        if not source.exists():
            return None
        self.dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        target = self.dir / f"{source.stem}.{ts}{source.suffix}"
        # Disambiguate same-microsecond collisions.
        n = 1
        while target.exists():
            target = self.dir / f"{source.stem}.{ts}-{n}{source.suffix}"
            n += 1
        shutil.copy2(source, target)
        self._prune(source.stem, source.suffix)
        return target

    # Backwards-compatible no-op kept so existing callers don't crash.
    def create_backup(self) -> None:
        pass

    def _prune(self, stem: str, suffix: str) -> None:
        candidates = sorted(self.dir.glob(f"{stem}.*{suffix}"))
        excess = len(candidates) - self.max_backups
        if excess <= 0:
            return
        for old in candidates[:excess]:
            try:
                old.unlink()
            except FileNotFoundError:
                pass
