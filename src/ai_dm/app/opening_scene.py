"""Build the opening "where you are" narration emitted at game start.

After ``_apply_hardcoded_start`` has activated the start scene and spawned
the player token, the player is staring at a map with no in-fiction
context. This module assembles a deterministic narration envelope from
the campaign pack's location data so the player gets:

  * a scene header with the location name,
  * a description of the place (from ``locations/*/nodes.json``),
  * an explicit list of *interactables* (anchors with meaningful tags,
    named features on the node, scene-resident NPCs) so the player can
    tell concrete affordances apart from atmospheric prose.

The envelope is shaped like the narration events emitted by
:class:`PlayerInputDispatcher` (so the existing Foundry chat renderer
already knows how to display the prose), plus two extra fields:

    interactables: [{name, kind, hint?}]   # things you can act on
    exits:         [{name, to?}]           # named exits from this node
    metadata:      {kind: "opening", scene_id, scene_name}

Renderer-side, ``narration_renderer.js`` reads the new fields and shows
them as distinct sections under the prose.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

from ai_dm.campaign.pack import CampaignPack

logger = logging.getLogger("ai_dm.app.opening_scene")

# Anchor tags we surface as interactable affordances. Travel-only tags
# (``travel``, ``difficult_terrain``) and pure spawn markers are skipped
# so the list shows the player meaningful things to act on, not noise.
_INTERESTING_ANCHOR_TAGS = frozenset({
    "entrance", "exit", "landmark", "view",
    "quest", "loot", "ruin", "holy", "hazard",
    "locked", "door",
})
_BORING_TAGS = frozenset({"travel", "difficult_terrain"})


# --------------------------------------------------------------------- #
# Pack readers
# --------------------------------------------------------------------- #


def _iter_json_files(root: Path, filename: str) -> Iterable[dict]:
    if not root.exists():
        return []
    out: list[dict] = []
    for path in sorted(root.rglob(filename)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("opening: %s unreadable: %s", path, exc)
            continue
        out.append(data if isinstance(data, dict) else {"_list": data})
    return out


def find_scene_node(pack: CampaignPack, scene_id: str) -> dict | None:
    """Return the node entry for ``scene_id`` from any ``nodes.json``."""
    for blob in _iter_json_files(pack.paths.locations, "nodes.json"):
        for node in blob.get("nodes", []) or []:
            if node.get("id") == scene_id:
                return node
    return None


def find_chapter_scene(pack: CampaignPack, scene_id: str) -> dict | None:
    """Return the chapter-scene entry whose ``id`` matches ``scene_id``.

    Chapter scenes (``chapters/<chap>/scenes.json``) carry narrative
    metadata — name, summary, ``location_id`` pointing at the
    ``locations/<loc>/`` folder, and per-scene anchors. They aren't
    indexed in ``locations/*/nodes.json`` (those are physical map
    nodes), so when the planner tracks a chapter scene as the current
    scene, ``find_scene_node`` correctly returns ``None`` — but the
    LLM still needs *something* to anchor "where am I" to. This
    helper exposes that mapping.
    """
    if not scene_id:
        return None
    for blob in _iter_json_files(pack.paths.chapters, "scenes.json"):
        for sc in blob.get("scenes") or []:
            if sc.get("id") == scene_id:
                return sc
    return None


def find_scene_anchors(pack: CampaignPack, scene_id: str) -> list[dict]:
    """Return the anchors block for ``scene_id`` from ``scene_locations.json``."""
    out: list[dict] = []
    for blob in _iter_json_files(pack.paths.locations, "scene_locations.json"):
        # scene_locations.json is a top-level list, so _iter_json_files wraps
        # it in {"_list": [...]}.
        scenes = blob.get("_list") if "_list" in blob else [blob]
        for scene in scenes or []:
            if (scene or {}).get("scene_id") != scene_id:
                continue
            out.extend(scene.get("anchors") or [])
    return out


def find_scene_npcs(pack: CampaignPack, scene_id: str) -> list[dict]:
    out: list[dict] = []
    for blob in _iter_json_files(pack.paths.locations, "npcs.json"):
        for npc in blob.get("npcs", []) or []:
            if npc.get("scene_id") == scene_id:
                out.append(npc)
    return out


def find_chapter_goal(pack: CampaignPack) -> str | None:
    """Extract a one-line "where you're headed" goal from the active
    chapter's ``summary.md``.

    Strategy: read ``chapters/<first_chapter>/summary.md`` (or the first
    chapter directory when the manifest has no entrypoint), find the
    "Core goals" section, and return the first bullet. Returns ``None``
    when no chapter, no summary, or no parseable goal exists.
    """
    chap_id = (pack.manifest.entrypoints or {}).get("first_chapter")
    chapters_root = pack.paths.chapters
    if not chapters_root or not chapters_root.exists():
        return None
    candidate_dirs: list[Path] = []
    if chap_id:
        candidate_dirs.append(chapters_root / chap_id)
    # Fall back to the first chapter directory by name.
    for d in sorted(chapters_root.iterdir()):
        if d.is_dir() and d not in candidate_dirs:
            candidate_dirs.append(d)
    for d in candidate_dirs:
        summary_path = d / "summary.md"
        if not summary_path.exists():
            continue
        try:
            text = summary_path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("chapter summary %s unreadable: %s", summary_path, exc)
            continue
        # Look for the first bullet under a "## Core goals" (or
        # similar) heading; fall back to the first non-blank,
        # non-heading line.
        lines = text.splitlines()
        in_goals = False
        for line in lines:
            s = line.strip()
            if s.lower().startswith("## ") and ("goal" in s.lower() or "objective" in s.lower()):
                in_goals = True
                continue
            if in_goals:
                if s.startswith(("- ", "* ")):
                    return s[2:].strip()
                if s.startswith("## "):
                    break  # next section
        # Fallback: first prose paragraph after the title.
        for line in lines:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            return s
    return None


# --------------------------------------------------------------------- #
# Envelope assembly
# --------------------------------------------------------------------- #


def _humanise(name: str) -> str:
    return str(name or "").replace("_", " ").strip() or ""


def _anchor_kind(tags: list[str]) -> str:
    s = set(tags or ())
    if "exit" in s or "door" in s:
        return "exit"
    if "entrance" in s:
        return "entrance"
    if "hazard" in s:
        return "hazard"
    if "loot" in s or "quest" in s:
        return "object"
    return "anchor"


def _collect_interactables(
    node: dict | None,
    anchors: list[dict],
    npcs: list[dict],
) -> list[dict]:
    """Merge node features + interesting anchors + scene NPCs into a
    flat ``[{name, kind, hint?, description?, interactions?}]`` list,
    deduped by lowercase name.

    Authored fields (``description``, ``interactions``, ``hint``) are
    forwarded verbatim so they reach the LLM via ``scene_brief``. The
    narrator prompt instructs the model to read those fields when the
    player engages with an interactable, which is how we expose
    affordances like "search for the silver tithe bowl, DC 12
    Perception" without hard-coding mechanics into the engine.
    """
    out: list[dict] = []
    seen: set[str] = set()

    def _push(
        name: str,
        kind: str,
        *,
        hint: str | None = None,
        description: str | None = None,
        interactions: list | None = None,
    ) -> None:
        clean = _humanise(name)
        key = clean.lower()
        if not clean or key in seen:
            return
        seen.add(key)
        entry: dict[str, Any] = {"name": clean, "kind": kind}
        if hint:
            entry["hint"] = hint
        if description:
            entry["description"] = description.strip()
        if interactions:
            # Keep only well-shaped action dicts (verb + at least one
            # other field) so a stray scalar in an authored file
            # doesn't poison the LLM context.
            cleaned = [
                ix for ix in interactions
                if isinstance(ix, dict) and ix.get("verb")
            ]
            if cleaned:
                entry["interactions"] = cleaned
        out.append(entry)

    # Named features on the node (these are explicitly authored as
    # interactable, so they go first).
    for feat in (node or {}).get("features", []) or []:
        if not isinstance(feat, dict):
            continue
        if feat.get("interactable") is False:
            continue
        _push(
            feat.get("name") or feat.get("id"),
            "object",
            description=feat.get("description"),
            interactions=feat.get("interactions"),
        )

    # NPCs in this scene.
    for npc in npcs:
        _push(
            npc.get("name") or npc.get("id"),
            "npc",
            hint=npc.get("disposition") or None,
            description=npc.get("description"),
            interactions=npc.get("interactions"),
        )

    # Anchors with meaningful tags. Skip pure travel/zone markers.
    for a in anchors:
        tags = list(a.get("tags") or ())
        if not tags or _BORING_TAGS.issuperset(tags):
            continue
        if not (set(tags) & _INTERESTING_ANCHOR_TAGS):
            continue
        _push(
            a.get("name") or a.get("id"),
            _anchor_kind(tags),
            description=a.get("description"),
            interactions=a.get("interactions"),
        )

    return out


def _collect_exits(node: dict | None) -> list[dict]:
    exits = (node or {}).get("exits") or {}
    if not isinstance(exits, dict):
        return []
    return [
        {"name": _humanise(direction), "to": dest}
        for direction, dest in exits.items()
        if direction
    ]


def _compose_prose(
    description: str,
    scene_name: str,
    interactables: list[dict],
    exits: list[dict],
) -> str:
    """Stitch a single coherent paragraph the player can read straight
    through — no bullet lists. Interactables and exits are folded into
    the narrative so concrete affordances live alongside atmosphere
    instead of in a separate UI block.
    """
    parts: list[str] = []
    parts.append(description.strip() if description else f"You arrive at {scene_name}.")

    # Group interactables by kind so the prose reads naturally:
    #   "Old Beren stands here."   (npc)
    #   "You spot a dry well and a ruined altar." (object/anchor)
    npcs = [i for i in interactables if i.get("kind") == "npc"]
    things = [i for i in interactables if i.get("kind") != "npc"]

    if npcs:
        names = _human_join([n["name"] for n in npcs if n.get("name")])
        verb = "stands" if len(npcs) == 1 else "stand"
        parts.append(f"{names} {verb} here.")

    if things:
        names = _human_join([_a_or_an(t["name"]) for t in things if t.get("name")])
        parts.append(f"You can see {names}.")

    if exits:
        ex_names = [e["name"] for e in exits if e.get("name")]
        if ex_names:
            parts.append(f"Exits: {_human_join(ex_names)}.")

    return " ".join(p for p in parts if p)


def _human_join(names: list[str]) -> str:
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _a_or_an(name: str) -> str:
    """Crude article picker so the woven prose reads naturally.

    Skips proper nouns (capitalised first letter) since "a Dry Well"
    looks worse than "Dry Well" — leave authored names untouched.
    """
    n = (name or "").strip()
    if not n:
        return n
    if n[:1].isupper():
        return n  # proper noun / authored name
    article = "an" if n[:1].lower() in "aeiou" else "a"
    return f"{article} {n}"


def build_opening_envelope(
    pack: CampaignPack,
    *,
    scene_id: str,
    actor_id: str | None,
    user_id: str | None,
) -> dict[str, Any] | None:
    """Assemble the opening narration envelope from pack data.

    Returns ``None`` if the pack has no information about ``scene_id``
    (no node entry and no anchors) — in that case the caller should not
    push any opening message.
    """
    node = find_scene_node(pack, scene_id)
    anchors = find_scene_anchors(pack, scene_id)
    npcs = find_scene_npcs(pack, scene_id)

    if node is None and not anchors and not npcs:
        return None

    scene_name = (node or {}).get("name") or _humanise(scene_id).title()
    description = ((node or {}).get("description") or "").strip()
    interactables = _collect_interactables(node, anchors, npcs)
    exits = _collect_exits(node)

    # Single prose paragraph (no bullet list) — interactables and exits
    # are woven into the narrative so the player sees atmosphere and
    # concrete affordances together.
    narration = _compose_prose(description, scene_name, interactables, exits)

    # Append the chapter's primary goal so the player knows where they
    # are *trying to get to* — without this the opener establishes
    # mood but not direction.
    goal = find_chapter_goal(pack)
    if goal:
        narration = f"{narration}\n\n**Your goal:** {goal}"

    envelope: dict[str, Any] = {
        "actor_id": actor_id,
        "user_id": user_id,
        "narration": narration,
        "dialogue": [],
        "commands_ok": True,
        # Opening narration is public: the player AND the GM (and any
        # other players watching) need to see the scene-start banner.
        # Whispering to a single user_id used to hide the opener from
        # the GM, who then had no idea play had begun.
        "whisper_to": None,
        "interactables": interactables,
        "exits": exits,
        "metadata": {
            "kind": "opening",
            "scene_id": scene_id,
            "scene_name": scene_name,
            "chapter_goal": goal,
        },
    }
    return envelope


# --------------------------------------------------------------------- #
# Scene brief — used on every narrator turn so the player keeps a sense
# of what's available, not just at scene start.
# --------------------------------------------------------------------- #


def build_scene_brief(
    pack: CampaignPack,
    scene_id: str | None,
) -> dict[str, Any] | None:
    """Return structured scene affordances for the LLM context.

    Shape::

        {
          "scene_id":   "courtyard",
          "scene_name": "Inner Courtyard",
          "interactables": [{name, kind, hint?}, ...],
          "exits":        [{name, to?}, ...],
          "summary":      "Around you: X, Y. NPCs here: Z. Exits: A, B.",
        }

    Falls back to the manifest's start scene when the caller's
    ``scene_id`` doesn't match any pack node (this happens often: chat
    events carry Foundry's opaque scene id, not the campaign slug).
    """
    if pack is None:
        return None

    candidates: list[str] = []
    if scene_id:
        candidates.append(scene_id)
    # If ``scene_id`` is a chapter scene id (chapters/<chap>/scenes.json),
    # also probe its ``location_id`` so the brief can find the
    # underlying physical node. Without this the lookup falls all the
    # way back to the manifest's start scene and the LLM keeps
    # describing the *first* location after the player has travelled
    # several scenes deep.
    chapter_scene = find_chapter_scene(pack, scene_id) if scene_id else None
    if chapter_scene:
        loc_id = chapter_scene.get("location_id")
        if loc_id and loc_id not in candidates:
            candidates.append(loc_id)
    start_scene = (pack.manifest.start or {}).get("scene")
    if start_scene and start_scene not in candidates:
        candidates.append(start_scene)

    node = None
    chosen = None
    for cand in candidates:
        node = find_scene_node(pack, cand)
        if node is not None:
            chosen = cand
            break

    # Even if no node matches, anchors / npcs may still exist on the
    # pack-side scene id — try those too.
    anchors: list[dict] = []
    npcs: list[dict] = []
    for cand in (chosen, *candidates):
        if not cand:
            continue
        anchors = find_scene_anchors(pack, cand)
        npcs = find_scene_npcs(pack, cand)
        if anchors or npcs:
            chosen = cand
            break

    if node is None and not anchors and not npcs and chapter_scene is None:
        return None

    interactables = _collect_interactables(node, anchors, npcs)
    exits = _collect_exits(node)
    # Prefer the chapter-scene name when we matched via chapter scenes —
    # the player asked "where am I" expecting the *narrative* scene
    # name ("The Haunted House"), not the parent location's id.
    scene_name = (
        (chapter_scene or {}).get("name")
        or (node or {}).get("name")
        or _humanise(chosen or scene_id or "").title()
    )

    # Compact spoken summary the LLM can quote verbatim or paraphrase.
    things = [_a_or_an(i["name"]) for i in interactables
              if i.get("kind") != "npc" and i.get("name")]
    npc_names = [i["name"] for i in interactables
                 if i.get("kind") == "npc" and i.get("name")]
    ex_names = [e["name"] for e in exits if e.get("name")]
    parts: list[str] = []
    # Lead the summary with the chapter scene's authored one-liner so
    # the LLM has narrative grounding for the *current* scene even
    # when no anchors/npcs/exits are authored at the location-node
    # level. Keeps "where am I" honest after travel.
    if chapter_scene and chapter_scene.get("summary"):
        parts.append(str(chapter_scene["summary"]).strip())
    if things:
        parts.append("Around you: " + _human_join(things) + ".")
    if npc_names:
        parts.append("NPCs here: " + _human_join(npc_names) + ".")
    if ex_names:
        parts.append("Exits: " + _human_join(ex_names) + ".")
    # Per-interactable affordance lines so the LLM can answer
    # "what can I do at the altar?" without re-deriving it from
    # mood prose. The structured ``interactions`` list is also
    # passed in scene_brief for richer reasoning.
    for entry in interactables:
        actions = entry.get("interactions") or []
        if not actions:
            continue
        verbs = []
        for ix in actions:
            v = ix.get("verb")
            if not v:
                continue
            label = ix.get("summary") or ix.get("narration") or _humanise(v)
            check = ix.get("check")
            dc = ix.get("dc")
            if check and dc is not None:
                label = f"{label} ({check.upper()} DC {dc})"
            elif check:
                label = f"{label} ({check.upper()})"
            verbs.append(label)
        if verbs:
            parts.append(f"At the {entry['name']} you can: " + "; ".join(verbs) + ".")
    summary = " ".join(parts)

    return {
        "scene_id": chosen or scene_id,
        "scene_name": scene_name,
        "interactables": interactables,
        "exits": exits,
        "summary": summary,
    }
