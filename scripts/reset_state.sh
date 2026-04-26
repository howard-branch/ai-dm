#!/usr/bin/env bash
# Reset writable AI-DM state so the next launch prompts for character
# creation through Foundry. Pack content (under ~/dnd/campaigns/) is
# left untouched — only the per-campaign mutable state under
# data/campaigns/<slug>/ and the legacy data/ dirs are wiped.
#
# Usage:
#     scripts/reset_state.sh                    # all known packs
#     scripts/reset_state.sh ArmyOfTheDamned    # specific slug
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

slugs=()
keep_foundry=0
for arg in "$@"; do
  case "$arg" in
    --keep-foundry) keep_foundry=1 ;;
    --*) echo "  [warn] unknown flag $arg" ;;
    *)   slugs+=("$arg") ;;
  esac
done
if [ "${#slugs[@]}" -eq 0 ]; then
  if [ -d data/campaigns ]; then
    while IFS= read -r d; do
      slugs+=("$(basename "$d")")
    done < <(find data/campaigns -mindepth 1 -maxdepth 1 -type d)
  fi
fi

echo "Resetting per-campaign state under data/campaigns/ for: ${slugs[*]:-<none>}"
for slug in "${slugs[@]}"; do
  base="data/campaigns/$slug"
  if [ ! -d "$base" ]; then
    echo "  [skip] $base does not exist"
    continue
  fi
  for sub in characters saves memory cache; do
    target="$base/$sub"
    if [ -e "$target" ]; then
      rm -rf "$target"
      echo "  [wipe] $target"
    fi
  done
done

echo "Wiping legacy data/saves and data/memory roots"
for legacy in data/saves data/memory data/cache; do
  if [ -d "$legacy" ]; then
    find "$legacy" -mindepth 1 -delete
    echo "  [wipe] $legacy/*"
  fi
done

# Drop a sentinel so the next ``python -m ai_dm.main`` knows to send
# the Foundry GM client a ``clear_chat`` event — wiping the in-Foundry
# chat sidebar and the persistent narration-log window so the new
# session starts visually clean.
mkdir -p data/cache
touch data/cache/clear_chat_on_next_start
echo "  [flag] data/cache/clear_chat_on_next_start"

# Second sentinel: also wipe Foundry world state (scenes / actors /
# tokens / notes / journals) created by the AI DM. The Python bootstrap
# computes the exact names to delete from the active campaign pack and
# sends them to the GM client; nothing outside the pack is touched.
# Pass `--keep-foundry` to skip this (useful when iterating on a
# Foundry-side bug without re-running the world-setup batch).
if [ "$keep_foundry" -eq 0 ]; then
  touch data/cache/reset_foundry_on_next_start
  echo "  [flag] data/cache/reset_foundry_on_next_start"
else
  echo "  [skip] reset_foundry_on_next_start (--keep-foundry)"
fi

echo
echo "Done. Next 'python -m ai_dm.main' will prompt for character"
echo "creation through the connected Foundry browser."

