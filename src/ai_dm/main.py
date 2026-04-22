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
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    if args.new_character:
        os.environ["AI_DM_NEW_CHARACTER"] = "1"
    # Load .env (if present) before anything reads OPENAI_API_KEY etc.
    load_dotenv()
    runtime = build_runtime()
    runtime.start()


if __name__ == "__main__":
    main()
