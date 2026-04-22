from __future__ import annotations

import argparse
import os

from ai_dm.app.bootstrap import build_runtime
from ai_dm.utils.dotenv import load_dotenv


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
