"""Shared loader for the SRD 5.2 core JSON catalog under
``assets/srd5_2/core/``.

These files are the *single source of truth* shared between the Python
rules engine and the Foundry-side mirror (``foundry/module/scripts/srd``).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
CORE_DIR = _REPO_ROOT / "assets" / "srd5_2" / "core"


@lru_cache(maxsize=None)
def load(name: str) -> dict[str, Any]:
    """Load ``assets/srd5_2/core/<name>.json`` and cache the result."""
    path = CORE_DIR / f"{name}.json"
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def core_dir() -> Path:
    return CORE_DIR


__all__ = ["CORE_DIR", "core_dir", "load"]

