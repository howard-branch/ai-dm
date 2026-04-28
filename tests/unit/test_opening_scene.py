"""Tests for the deterministic opening narration assembler."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_dm.app.opening_scene import (
    build_opening_envelope,
    build_scene_brief,
    find_scene_anchors,
    find_scene_node,
    find_scene_npcs,
)
from ai_dm.campaign.pack import CampaignPack


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture()
def pack(tmp_path: Path) -> CampaignPack:
    root = tmp_path / "pack"
    state_root = tmp_path / "state"
    (root / "prompts").mkdir(parents=True)
    (root / "campaign.yaml").write_text(
        "id: testpack\nname: Test\nstart:\n  scene: courtyard\n  player_character: pc\n",
        encoding="utf-8",
    )

    locs = root / "locations" / "keep"
    _write(locs / "nodes.json", {
        "nodes": [
            {
                "id": "courtyard",
                "name": "Inner Courtyard",
                "description": "A cloistered limestone square at dusk.",
                "exits": {"gate": "outer_gate", "scriptorium": "scriptorium"},
                "features": [
                    {"id": "object.well", "name": "Dry Well", "interactable": True},
                    {"id": "object.flag", "name": "Hidden Flag", "interactable": False},
                ],
            }
        ]
    })
    _write(locs / "scene_locations.json", [
        {
            "scene_id": "courtyard",
            "anchors": [
                {"id": "anchor.gate", "name": "gate_inner", "scene_id": "courtyard",
                 "x": 0, "y": 0, "tags": ["entrance"]},
                {"id": "anchor.path", "name": "main_path", "scene_id": "courtyard",
                 "x": 0, "y": 0, "tags": ["travel"]},
                {"id": "anchor.altar", "name": "ruined_altar", "scene_id": "courtyard",
                 "x": 0, "y": 0, "tags": ["quest", "holy"]},
            ],
            "zones": [],
        }
    ])
    _write(locs / "npcs.json", {
        "npcs": [
            {"id": "npc.beren", "name": "Old Beren", "scene_id": "courtyard",
             "disposition": "neutral"},
            {"id": "npc.elsewhere", "name": "Far Away", "scene_id": "other_scene"},
        ]
    })

    return CampaignPack.load(root, state_root=state_root)


def test_find_scene_node_walks_locations(pack: CampaignPack) -> None:
    node = find_scene_node(pack, "courtyard")
    assert node is not None
    assert node["name"] == "Inner Courtyard"


def test_find_scene_anchors_filters_by_scene_id(pack: CampaignPack) -> None:
    anchors = find_scene_anchors(pack, "courtyard")
    assert {a["name"] for a in anchors} == {"gate_inner", "main_path", "ruined_altar"}


def test_find_scene_npcs_filters_by_scene_id(pack: CampaignPack) -> None:
    npcs = find_scene_npcs(pack, "courtyard")
    assert [n["name"] for n in npcs] == ["Old Beren"]


def test_build_opening_envelope_assembles_full_payload(pack: CampaignPack) -> None:
    env = build_opening_envelope(pack, scene_id="courtyard",
                                 actor_id="pc", user_id="userX")
    assert env is not None
    # Header metadata
    assert env["metadata"]["kind"] == "opening"
    assert env["metadata"]["scene_id"] == "courtyard"
    assert env["metadata"]["scene_name"] == "Inner Courtyard"
    # Prose: starts with the description and weaves NPCs, things and
    # exits into the same paragraph (no separate bullet list).
    prose = env["narration"]
    assert prose.startswith("A cloistered limestone square")
    assert "Old Beren" in prose                   # NPC
    assert "stands here" in prose
    assert "Dry Well" in prose                    # node feature
    assert "ruined altar" in prose                # interesting anchor
    assert "gate inner" in prose                  # entrance anchor
    assert "main path" not in prose               # boring travel anchor filtered
    assert "Hidden Flag" not in prose             # interactable=False skipped
    assert "Exits:" in prose and "gate" in prose and "scriptorium" in prose
    # Opening is public — visible to GM and player both. (Was
    # previously whispered to user_id, which hid it from the GM.)
    assert env["whisper_to"] is None
    assert env["user_id"] == "userX"
    # The structured fields are still present (used elsewhere, e.g. for
    # programmatic tools), but the renderer no longer shows them as a
    # separate list — the prose covers it.
    names = [i["name"] for i in env["interactables"]]
    assert names[0] == "Dry Well"
    assert "Old Beren" in names
    exit_names = {e["name"] for e in env["exits"]}
    assert exit_names == {"gate", "scriptorium"}


def test_build_opening_envelope_returns_none_for_unknown_scene(
    pack: CampaignPack,
) -> None:
    assert build_opening_envelope(
        pack, scene_id="nowhere", actor_id="pc", user_id=None,
    ) is None


def test_npc_hint_uses_disposition(pack: CampaignPack) -> None:
    env = build_opening_envelope(pack, scene_id="courtyard",
                                 actor_id="pc", user_id=None)
    assert env is not None
    npc_entry = next(i for i in env["interactables"] if i["name"] == "Old Beren")
    assert npc_entry["kind"] == "npc"
    assert npc_entry.get("hint") == "neutral"


# --------------------------------------------------------------------- #
# build_scene_brief — runtime affordance summary attached to every turn
# --------------------------------------------------------------------- #


def test_build_scene_brief_returns_summary_and_lists(pack: CampaignPack) -> None:
    brief = build_scene_brief(pack, "courtyard")
    assert brief is not None
    assert brief["scene_id"] == "courtyard"
    assert brief["scene_name"] == "Inner Courtyard"
    # Structured lists are present for tools that want them.
    names = {i["name"] for i in brief["interactables"]}
    assert "Old Beren" in names and "Dry Well" in names
    # Compact spoken summary covers things, NPCs and exits.
    s = brief["summary"]
    assert "Around you:" in s and "Dry Well" in s
    assert "NPCs here:" in s and "Old Beren" in s
    assert "Exits:" in s and "gate" in s and "scriptorium" in s


def test_build_scene_brief_falls_back_to_manifest_start(pack: CampaignPack) -> None:
    """A Foundry-supplied opaque scene id should still resolve to the
    pack's start scene so the brief is non-empty mid-game."""
    brief = build_scene_brief(pack, "abc123XYZopaque")
    assert brief is not None
    assert brief["scene_id"] == "courtyard"   # fell back to manifest start


def test_build_scene_brief_returns_none_when_no_match(tmp_path) -> None:
    """No nodes, no anchors, no NPCs → no brief."""
    from ai_dm.app.opening_scene import build_scene_brief
    from ai_dm.campaign.pack import CampaignPack
    root = tmp_path / "pack"
    state_root = tmp_path / "state"
    (root / "prompts").mkdir(parents=True)
    (root / "campaign.yaml").write_text(
        "id: bare\nname: Bare\nstart:\n  scene: nowhere\n  player_character: pc\n",
        encoding="utf-8",
    )
    bare = CampaignPack.load(root, state_root=state_root)
    assert build_scene_brief(bare, "nowhere") is None


# --------------------------------------------------------------------- #
# Authored description + interactions surface in scene_brief
# --------------------------------------------------------------------- #


def test_authored_interactions_flow_into_scene_brief(tmp_path: Path) -> None:
    """Features and NPCs may carry ``description`` + ``interactions``;
    both must round-trip into ``scene_brief.interactables`` and the
    summary must surface a per-interactable affordance line so the
    LLM can answer "what can I do at the altar?" without inventing
    mechanics."""
    from ai_dm.app.opening_scene import build_scene_brief
    from ai_dm.campaign.pack import CampaignPack

    root = tmp_path / "pack"
    state_root = tmp_path / "state"
    (root / "prompts").mkdir(parents=True)
    (root / "campaign.yaml").write_text(
        "id: rich\nname: Rich\nstart:\n  scene: chapel\n  player_character: pc\n",
        encoding="utf-8",
    )
    locs = root / "locations" / "stensia"
    _write(locs / "nodes.json", {
        "nodes": [
            {
                "id": "chapel",
                "name": "Ruined Chapel",
                "description": "A roofless chapel.",
                "features": [
                    {
                        "id": "object.altar",
                        "name": "Ruined Altar",
                        "interactable": True,
                        "tags": ["holy", "quest"],
                        "description": "A toppled altar at the nave's far end.",
                        "interactions": [
                            {
                                "verb": "search",
                                "summary": "Search the rubble beside the altar.",
                                "check": "wis.perception",
                                "dc": 12,
                                "on_success": "A silver tithe bowl: 17 cp, 14 sp, 2 gp.",
                                "grants": ["loot.silver_tithe_bowl"],
                            },
                            {
                                "verb": "pray",
                                "summary": "Help finish the unending rite.",
                                "check": "int.religion",
                                "dc": 12,
                                "ends_encounter": "encounter.ancient_devotees",
                            },
                            {"summary": "ignored — no verb"},  # malformed
                        ],
                    },
                ],
            }
        ]
    })
    pack = CampaignPack.load(root, state_root=state_root)

    brief = build_scene_brief(pack, "chapel")
    assert brief is not None

    altar = next(i for i in brief["interactables"] if i["name"] == "Ruined Altar")
    assert altar["description"] == "A toppled altar at the nave's far end."
    assert len(altar["interactions"]) == 2  # malformed entry dropped
    verbs = {ix["verb"] for ix in altar["interactions"]}
    assert verbs == {"search", "pray"}

    summary = brief["summary"]
    assert "At the Ruined Altar you can:" in summary
    assert "Search the rubble" in summary
    assert "WIS.PERCEPTION DC 12" in summary
    assert "INT.RELIGION DC 12" in summary


def test_npc_authored_interactions_surface(tmp_path: Path) -> None:
    """NPCs may also carry ``interactions`` (e.g. dialogue gating
    checks). They must reach the brief identically to feature/anchor
    interactions so the LLM can lean on them in conversation scenes."""
    from ai_dm.app.opening_scene import build_scene_brief
    from ai_dm.campaign.pack import CampaignPack

    root = tmp_path / "pack"
    state_root = tmp_path / "state"
    (root / "prompts").mkdir(parents=True)
    (root / "campaign.yaml").write_text(
        "id: rich2\nname: Rich2\nstart:\n  scene: gate\n  player_character: pc\n",
        encoding="utf-8",
    )
    locs = root / "locations" / "shadowgrange"
    _write(locs / "nodes.json", {
        "nodes": [{"id": "gate", "name": "Gate"}],
    })
    _write(locs / "npcs.json", {
        "npcs": [
            {
                "id": "npc.lars",
                "name": "Lars",
                "scene_id": "gate",
                "disposition": "friendly",
                "description": "A cathar guard cheerful with strangers.",
                "interactions": [
                    {
                        "verb": "ask_for_directions",
                        "summary": "Ask Lars where to find someone in the village.",
                    },
                ],
            }
        ]
    })
    pack = CampaignPack.load(root, state_root=state_root)
    brief = build_scene_brief(pack, "gate")
    assert brief is not None
    lars = next(i for i in brief["interactables"] if i["name"] == "Lars")
    assert lars["description"].startswith("A cathar guard")
    assert lars["interactions"][0]["verb"] == "ask_for_directions"
    assert "At the Lars you can: Ask Lars where" in brief["summary"] \
        or "At the Lars you can: ask_for_directions" in brief["summary"]


