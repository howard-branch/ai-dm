from __future__ import annotations

import argparse
import logging
import logging.config
import os
from pathlib import Path

from ai_dm.app.bootstrap import build_runtime
from ai_dm.utils.dotenv import load_dotenv


def _configure_logging() -> None:
    """Load ``config/logging.yaml`` if present, else fall back to a
    sane console default.

    Without this, Python's root logger sits at WARNING with no handler
    attached, which is why all the ``ai_dm.*`` ``logger.info(...)``
    calls were silently dropped — including the entire ``npc_turn:``
    diagnostic stream.
    """
    cfg_path = Path(__file__).resolve().parents[2] / "config" / "logging.yaml"
    if cfg_path.exists():
        try:
            import yaml  # type: ignore[import-not-found]
            with cfg_path.open("r", encoding="utf-8") as fh:
                logging.config.dictConfig(yaml.safe_load(fh))
            logging.getLogger("ai_dm").info(
                "logging configured from %s", cfg_path,
            )
            return
        except Exception as exc:  # noqa: BLE001
            print(f"[ai-dm] failed to load {cfg_path}: {exc}; using basicConfig")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="ai-dm")
    p.add_argument(
        "--new-character",
        action="store_true",
        help="Force the guided character-creation wizard at startup, "
             "even if a sheet already exists for the active campaign's PC.",
    )
    p.add_argument(
        "--voice",
        action="store_true",
        help="Start in hands-free voice mode (record on silence, transcribe, send). "
             "Say 'stop listening' or press Ctrl-C to return to text input.",
    )
    return p.parse_args()


def main() -> None:
    # Configure logging FIRST so module-level logger creation in
    # bootstrap (and everything it imports) inherits the right levels
    # and the console handler.
    _configure_logging()
    args = _parse_args()
    if args.new_character:
        os.environ["AI_DM_NEW_CHARACTER"] = "1"
    # Load .env (if present) before anything reads OPENAI_API_KEY etc.
    load_dotenv()
    runtime = build_runtime()
    if args.voice:
        runtime._voice_on_start = True  # noqa: SLF001 — public-enough toggle
    runtime.start()


if __name__ == "__main__":
    main()
