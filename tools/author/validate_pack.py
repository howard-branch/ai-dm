#!/usr/bin/env python3
"""Pack linter for the AI DM scenario-adapter workflow.

Usage:
    python tools/author/validate_pack.py <pack-root>

Exits non-zero if any check fails. Designed to be re-fed to the
scenario adapter LLM verbatim so it can patch the offending file.

Checks (cheap, deterministic, no runtime dependencies):
  * Every JSON file under the pack parses.
  * Every node `exits` value points at a known scene-id (node id, or
    chapter scene id, or another node's id at any location).
  * Every feature `interactions[].grants` id is declared in some
    chapters/*/treasure.json.
  * Every feature `interactions[].starts_encounter` /
    `ends_encounter` references an encounter id declared in
    chapters/*/encounters.json.
  * Every chapters/*/scenes.json `location_id` matches a node id
    under locations/.
  * Every chapters/*/scenes.json beat `completes_on.encounter_id`
    references a known encounter id.
  * Every NPC `default_anchor` matches an anchor id in the same
    location's scene_locations.json.
  * Every feature description mentions every noun that the feature's
    interaction `verb`s reference (heuristic — flags missing
    "rubble" / "circle" / "altar" / "podium" / etc. that the verb
    name implies).
  * Every triggers/*.json `do[].speak.text` is non-empty and every
    `start_encounter.encounter_id` references a known encounter id.

This script is content-only — it never imports ai_dm.* and never
hits the network, so it can run in CI alongside the engine tests.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _ParseError(path, str(exc))


class _ParseError:
    def __init__(self, path: Path, msg: str) -> None:
        self.path = path
        self.msg = msg


def _iter_files(root: Path, glob: str):
    return sorted(root.glob(glob))


def _collect_loot_ids(pack: Path) -> set[str]:
    out: set[str] = set()
    for path in _iter_files(pack, "chapters/*/treasure.json"):
        data = _load_json(path)
        if isinstance(data, _ParseError):
            continue
        if isinstance(data, dict):
            data = data.get("loot") or data.get("treasure") or list(data.values())
        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict) and entry.get("id"):
                    out.add(entry["id"])
    return out


def _collect_encounter_ids(pack: Path) -> set[str]:
    out: set[str] = set()
    for path in _iter_files(pack, "chapters/*/encounters.json"):
        data = _load_json(path)
        if isinstance(data, _ParseError):
            continue
        if isinstance(data, dict):
            data = data.get("encounters") or list(data.values())
        if isinstance(data, list):
            for e in data:
                if isinstance(e, dict) and e.get("id"):
                    out.add(e["id"])
    return out


def _collect_node_ids_by_loc(pack: Path) -> dict[str, set[str]]:
    """{location_dir_name: {node_id, ...}}"""
    out: dict[str, set[str]] = {}
    for path in _iter_files(pack, "locations/*/nodes.json"):
        loc = path.parent.name
        data = _load_json(path)
        if isinstance(data, _ParseError):
            continue
        ids: set[str] = set()
        for n in (data or {}).get("nodes", []):
            if isinstance(n, dict) and n.get("id"):
                ids.add(n["id"])
        out[loc] = ids
    return out


def _collect_chapter_scene_ids(pack: Path) -> set[str]:
    out: set[str] = set()
    for path in _iter_files(pack, "chapters/*/scenes.json"):
        data = _load_json(path)
        if isinstance(data, _ParseError):
            continue
        for s in (data or {}).get("scenes", []):
            if isinstance(s, dict) and s.get("id"):
                out.add(s["id"])
    return out


def _collect_anchor_ids_by_loc(pack: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in _iter_files(pack, "locations/*/scene_locations.json"):
        loc = path.parent.name
        data = _load_json(path)
        if isinstance(data, _ParseError):
            continue
        ids: set[str] = set()
        if isinstance(data, list):
            for scene in data:
                for a in (scene or {}).get("anchors") or []:
                    if isinstance(a, dict) and a.get("id"):
                        ids.add(a["id"])
        out[loc] = ids
    return out


# Heuristic: pull "thematic noun" out of a verb. "search_rubble" → "rubble".
# Skips generic verbs (search, examine, look, talk, …) so we only flag the
# *object* the player is supposed to be acting on.
_GENERIC_VERB_PARTS = {
    "search", "examine", "look", "inspect", "investigate", "study",
    "talk", "speak", "ask", "tell", "shout", "whisper",
    "open", "close", "lock", "unlock", "force", "pull", "push",
    "use", "read", "drink", "eat", "throw", "drop", "pick", "up",
    "approach", "walk", "step", "enter", "exit", "leave", "go", "head",
    "attack", "draw", "swing", "shoot", "cast", "channel", "summon",
    "join", "begin", "start", "end", "finish", "stop",
    "loot", "take", "grab", "carry", "lift", "move",
    "hide", "sneak", "stealth", "scout",
    "to", "the", "a", "an", "of", "from", "with", "into", "at",
    "pray", "meditate", "rest", "wait",
    "break", "smash", "destroy", "shatter",
    "kneel", "stand", "sit", "lie",
    "expose", "shelter", "ward", "bless", "heal", "cure",
    "identify", "recall", "survey", "scan",
    "desecrate", "defile", "ruin",
    "ready", "aim", "release",
}


def _verb_object_nouns(verb: str) -> list[str]:
    parts = re.split(r"[_\s\-]+", (verb or "").lower())
    return [p for p in parts if p and p not in _GENERIC_VERB_PARTS and not p.isdigit()]


def _check_node_descriptions(
    pack: Path, errors: list[str]
) -> None:
    for path in _iter_files(pack, "locations/*/nodes.json"):
        data = _load_json(path)
        if isinstance(data, _ParseError):
            continue
        for node in (data or {}).get("nodes", []):
            for feat in (node or {}).get("features", []) or []:
                desc = (feat.get("description") or "").lower()
                if not desc:
                    continue
                missing: list[str] = []
                for ix in feat.get("interactions") or []:
                    for noun in _verb_object_nouns(ix.get("verb") or ""):
                        if noun not in desc and noun + "s" not in desc:
                            missing.append(noun)
                missing = sorted(set(missing))
                if missing:
                    errors.append(
                        f"{path}: feature {feat.get('id')!r} description does not "
                        f"mention noun(s) referenced by interaction verbs: {missing}. "
                        f"Add them to the description so the player can discover the action."
                    )


def _check_exits(
    pack: Path,
    nodes_by_loc: dict[str, set[str]],
    chapter_scene_ids: set[str],
    errors: list[str],
) -> None:
    all_node_ids: set[str] = set()
    for ids in nodes_by_loc.values():
        all_node_ids |= ids
    valid_dests = all_node_ids | chapter_scene_ids
    for path in _iter_files(pack, "locations/*/nodes.json"):
        data = _load_json(path)
        if isinstance(data, _ParseError):
            continue
        for node in (data or {}).get("nodes", []):
            for direction, dest in ((node or {}).get("exits") or {}).items():
                if dest not in valid_dests:
                    errors.append(
                        f"{path}: node {node.get('id')!r} exit {direction!r} → "
                        f"{dest!r}: no such node or chapter scene"
                    )


def _check_interactions(
    pack: Path,
    loot_ids: set[str],
    encounter_ids: set[str],
    errors: list[str],
) -> None:
    for path in _iter_files(pack, "locations/*/nodes.json"):
        data = _load_json(path)
        if isinstance(data, _ParseError):
            continue
        for node in (data or {}).get("nodes", []):
            for feat in (node or {}).get("features", []) or []:
                for ix in feat.get("interactions") or []:
                    if not isinstance(ix, dict):
                        continue
                    if not ix.get("verb"):
                        errors.append(
                            f"{path}: feature {feat.get('id')!r} has an "
                            f"interaction with no verb"
                        )
                    if ix.get("check") and ix.get("dc") is None:
                        errors.append(
                            f"{path}: feature {feat.get('id')!r} verb "
                            f"{ix.get('verb')!r} has check {ix['check']!r} "
                            f"but no dc"
                        )
                    for grant in ix.get("grants") or []:
                        if grant not in loot_ids:
                            errors.append(
                                f"{path}: feature {feat.get('id')!r} verb "
                                f"{ix.get('verb')!r} grants unknown loot id "
                                f"{grant!r} (declare it in chapters/*/treasure.json)"
                            )
                    for k in ("starts_encounter", "ends_encounter"):
                        eid = ix.get(k)
                        if eid and eid not in encounter_ids:
                            errors.append(
                                f"{path}: feature {feat.get('id')!r} verb "
                                f"{ix.get('verb')!r} {k} → {eid!r}: no such "
                                f"encounter (declare it in chapters/*/encounters.json)"
                            )
                    # Optional `xp` award on success. Must be a
                    # non-negative integer; warn (don't error) if it's
                    # set without a `check`/`dc` because the awarder
                    # only fires on a successful authored roll.
                    if "xp" in ix:
                        xp = ix.get("xp")
                        if not isinstance(xp, int) or xp < 0:
                            errors.append(
                                f"{path}: feature {feat.get('id')!r} verb "
                                f"{ix.get('verb')!r} xp must be a "
                                f"non-negative integer (got {xp!r})"
                            )
                        elif not ix.get("check") or ix.get("dc") is None:
                            errors.append(
                                f"{path}: feature {feat.get('id')!r} verb "
                                f"{ix.get('verb')!r} has xp={xp} but no "
                                f"check/dc — the award only fires on a "
                                f"successful roll, so it would never trigger"
                            )


def _check_chapter_scenes(
    pack: Path,
    nodes_by_loc: dict[str, set[str]],
    encounter_ids: set[str],
    errors: list[str],
) -> None:
    valid_loc_ids = set(nodes_by_loc.keys())
    for path in _iter_files(pack, "chapters/*/scenes.json"):
        data = _load_json(path)
        if isinstance(data, _ParseError):
            continue
        for scene in (data or {}).get("scenes", []):
            loc = scene.get("location_id")
            if loc and loc not in valid_loc_ids:
                errors.append(
                    f"{path}: scene {scene.get('id')!r} location_id={loc!r}: "
                    f"no matching directory under locations/"
                )
            for beat in scene.get("beats") or []:
                cond = beat.get("completes_on") or {}
                eid = cond.get("encounter_id")
                if eid and eid not in encounter_ids:
                    errors.append(
                        f"{path}: scene {scene.get('id')!r} beat "
                        f"{beat.get('id')!r} completes_on encounter_id={eid!r}: "
                        f"no such encounter"
                    )


def _check_npcs(
    pack: Path,
    anchors_by_loc: dict[str, set[str]],
    errors: list[str],
) -> None:
    for path in _iter_files(pack, "locations/*/npcs.json"):
        loc = path.parent.name
        data = _load_json(path)
        if isinstance(data, _ParseError):
            continue
        anchors = anchors_by_loc.get(loc, set())
        for npc in (data or {}).get("npcs", []):
            if not isinstance(npc, dict):
                continue
            anc = npc.get("default_anchor")
            if anc and anc not in anchors:
                errors.append(
                    f"{path}: npc {npc.get('id')!r} default_anchor={anc!r} "
                    f"not declared in locations/{loc}/scene_locations.json"
                )


def _check_triggers(
    pack: Path,
    encounter_ids: set[str],
    errors: list[str],
) -> None:
    for path in _iter_files(pack, "triggers/*.json"):
        data = _load_json(path)
        if isinstance(data, _ParseError):
            continue
        for trig in (data or {}).get("triggers", []):
            if not trig.get("id"):
                errors.append(f"{path}: trigger missing id")
            if not trig.get("event"):
                errors.append(f"{path}: trigger {trig.get('id')!r} missing event")
            for action in trig.get("do") or []:
                if not isinstance(action, dict) or len(action) != 1:
                    errors.append(
                        f"{path}: trigger {trig.get('id')!r} action must be a "
                        f"single-key dict, got {action!r}"
                    )
                    continue
                (op, args), = action.items()
                args = args or {}
                if op == "speak":
                    if not (args.get("text") or "").strip():
                        errors.append(
                            f"{path}: trigger {trig.get('id')!r} speak.text is empty"
                        )
                elif op == "start_encounter":
                    eid = args.get("encounter_id")
                    if eid and eid not in encounter_ids:
                        errors.append(
                            f"{path}: trigger {trig.get('id')!r} "
                            f"start_encounter→{eid!r}: no such encounter"
                        )


def validate_pack(pack: Path) -> list[str]:
    if not pack.exists():
        return [f"{pack}: pack root does not exist"]

    errors: list[str] = []

    # Phase 1: every JSON file parses.
    for path in pack.rglob("*.json"):
        result = _load_json(path)
        if isinstance(result, _ParseError):
            errors.append(f"{result.path}: invalid JSON: {result.msg}")
    if errors:
        # Bail early — the cross-file checks below assume parseable JSON.
        return errors

    # Phase 2: cross-file integrity.
    loot_ids = _collect_loot_ids(pack)
    encounter_ids = _collect_encounter_ids(pack)
    nodes_by_loc = _collect_node_ids_by_loc(pack)
    chapter_scene_ids = _collect_chapter_scene_ids(pack)
    anchors_by_loc = _collect_anchor_ids_by_loc(pack)

    _check_exits(pack, nodes_by_loc, chapter_scene_ids, errors)
    _check_interactions(pack, loot_ids, encounter_ids, errors)
    _check_chapter_scenes(pack, nodes_by_loc, encounter_ids, errors)
    _check_npcs(pack, anchors_by_loc, errors)
    _check_triggers(pack, encounter_ids, errors)
    _check_node_descriptions(pack, errors)

    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pack_root", help="Path to the campaign pack directory")
    args = ap.parse_args(argv)

    pack = Path(args.pack_root).expanduser().resolve()
    errors = validate_pack(pack)

    if not errors:
        print(f"OK: {pack} passes all author-time checks.")
        return 0

    print(f"FAIL: {pack} has {len(errors)} issue(s):", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

