"""Minimal, dependency-free ``.env`` loader.

We deliberately avoid pulling in ``python-dotenv``: this keeps the
runtime dependency surface small. The parser supports the common
subset:

* ``KEY=value`` lines.
* ``# ...`` comments and blank lines.
* Optional surrounding single or double quotes around the value.
* Lines starting with ``export `` (bash-style) are tolerated.

Existing environment variables are **never** overwritten — real shell
exports always win over the file. This matches ``python-dotenv``'s
default behaviour and avoids surprises in CI.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("ai_dm.utils.dotenv")


def load_dotenv(path: str | Path = ".env", *, override: bool = False) -> dict[str, str]:
    """Load ``path`` into ``os.environ``. Returns the parsed mapping.

    Missing files are a no-op (returns an empty dict). Malformed lines
    are skipped with a debug log; the loader never raises.
    """
    p = Path(path)
    if not p.is_file():
        return {}

    parsed: dict[str, str] = {}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("could not read %s: %s", p, exc)
        return {}

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            logger.debug("%s:%d skipping malformed line", p, lineno)
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        # Strip wrapping quotes, if balanced.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        parsed[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return parsed

