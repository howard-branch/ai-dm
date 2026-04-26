#!/usr/bin/env python3
"""Sync the SRD core JSON catalog into the Foundry module's asset tree.

Foundry serves files under ``/modules/<id>/...``, so the JSON the
JavaScript layer in ``foundry/module/scripts/srd/`` reads must live at
``foundry/module/assets/srd5_2/core/``. This script copies (or
hardlinks) the canonical files from ``assets/srd5_2/core/`` so the
two stay in lock-step.

Run from the repo root::

    python scripts/sync_foundry_assets.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "assets" / "srd5_2" / "core"
DST = REPO / "foundry" / "module" / "assets" / "srd5_2" / "core"


def sync() -> int:
    if not SRC.exists():
        print(f"missing source dir: {SRC}", file=sys.stderr)
        return 1
    DST.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in sorted(SRC.glob("*.json")):
        target = DST / path.name
        shutil.copyfile(path, target)
        copied += 1
        print(f"copied {path.relative_to(REPO)} -> {target.relative_to(REPO)}")
    print(f"\n{copied} file(s) synced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(sync())

