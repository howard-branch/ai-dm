# Scenario → Pack: authoring workflow

This document defines a repeatable process for translating a free-form
scenario (e.g. `armyofthedamned.txt`) into the structured campaign-pack
files the AI DM runtime consumes.

There are three artefacts that work together:

1. **This document** — describes *what* to extract and *where it goes*.
2. **`scenario_adapter_prompt.md`** — a drop-in LLM prompt that takes one
   scenario chunk and emits the JSON. Use it iteratively, one location
   or chapter at a time. Never feed the entire scenario at once.
3. **`validate_pack.py`** — a static linter that checks the emitted JSON
   for required fields, dangling references, encounter/anchor wiring,
   and JSON parseability. Run it after every adapter pass.

## 0. Audience & ground rules

The "scenario adapter" is whoever (or whichever LLM) is doing the
translation. The deliverable is **JSON files under
`<pack-root>/locations/<area>/`, `<pack-root>/chapters/<chapter_id>/`,
and `<pack-root>/triggers/`** that conform to the shapes documented in
section 2 below and pass `validate_pack.py`.

The adapter must follow these invariants on every pass:

* **Authored only.** Never invent NPCs, anchors, encounters or loot
  that the source scenario does not contain. The runtime narrator is
  instructed to refuse to fabricate mechanics the pack didn't author —
  if you invent it without a check/DC, the LLM has nothing to hang a
  ruling on at play time. If the source is silent, leave the field
  unset, not made-up.
* **Verbs, not prose.** Every interactable that should trigger a check,
  loot grant, encounter start, or scene transition must be expressed as
  an `interactions[]` entry with a `verb`. Prose that only describes
  atmosphere goes in `description`. The runtime cannot react to prose.
* **Reference, don't duplicate.** Loot ids, encounter ids and NPC ids
  are declared once (in `treasure.json`, `encounters.json`,
  `<location>/npcs.json`) and referenced by id elsewhere. No copy-paste
  stat blocks across files.
* **Idempotent passes.** The adapter prompt is deterministic given the
  same chunk + same existing files; re-running it should not duplicate
  entries. Use stable ids derived from names (`anchor.altar`,
  `encounter.ancient_devotees`) rather than counters.

## 1. End-to-end workflow

Per chapter / area, run these steps in order. Each step has an LLM
prompt section in `scenario_adapter_prompt.md`.

### Step A — Outline the chapter

Read one chapter of the source scenario. Produce a flat outline:

* Chapter id + title.
* List of distinct **scenes** (each scene is a beat the players
  physically reach: an arrival, a confrontation, a discovery).
* For each scene: the **location** it lives in (a "location" is a map
  + several anchors; multiple scenes may share a location).
* Story beats inside each scene (what completes the scene).

Output goes to `chapters/<chapter_id>/summary.md` (free prose) and is
the *seed* for steps B–E.

### Step B — Author locations

For each location:

1. `locations/<loc>/description.md` — long-form GM-facing prose copied
   nearly verbatim from the source (this is *reference*, not
   player-facing).
2. `locations/<loc>/nodes.json` — one entry per scene-id at this
   location, with:
   * `id`, `name`, `description` (player-facing prose, woven into
     the opening narration; must mention every interactable feature
     by name so the LLM can refer back to them).
   * `exits` — `{cardinal_or_named: dest_scene_id}`.
   * `features[]` — interactable objects. Each feature has its own
     `id`, `name`, `tags`, `description`, and `interactions[]` (see
     section 2.1).
3. `locations/<loc>/scene_locations.json` — anchors and zones per
   scene. Anchors are *named coordinates* on the Foundry map (entrance,
   altar, view, etc.); zones are rectangles tagging a region (travel,
   danger, holy …). The opening renderer uses tag-filtered anchors
   (entrance, exit, landmark, view, quest, loot, ruin, holy, hazard,
   locked, door) as interactables — do **not** invent generic
   coordinates; use ones the source scenario actually places objects
   at.
4. `locations/<loc>/npcs.json` — NPC roster for this location. Each
   entry binds a stable `npc.<slug>` id to a `default_anchor` and an
   optional `disposition`.
5. `locations/<loc>/events.json` — one-shot world events that happen
   at this location (used by triggers; usually empty on first pass).

### Step C — Author the chapter spine

Per chapter directory:

1. `chapters/<chap>/scenes.json` — the **narrative scenes** (different
   from physical nodes). Each scene declares:
   * `id`, `name`, `location_id` (the parent location node), `summary`,
     `time_of_day`, `starting_anchor`,
   * `beats[]` — completion conditions. A beat completes on a
     specific event (`combat.encounter_ended`, `intent.resolved`,
     `scene.entered`, …). Without these the planner can't advance.
2. `chapters/<chap>/encounters.json` — combat encounters keyed by
   `encounter.<slug>`. Each entry has `name`, `scene_id`,
   `difficulty`, `monsters[]` (`{name, count}` referencing monsters
   in `assets/srd5_2/monsters.json` or pack-local monster files), and
   a `victory.payload` block emitted on win.
3. `chapters/<chap>/treasure.json` — loot ids referenced by feature
   interactions' `grants[]`. Each loot id resolves to a structured
   reward (gold, items, magic items).
4. `chapters/<chap>/npcs.json` — chapter-scope NPCs that are not pinned
   to a single location (recurring contacts, antagonists).
5. `chapters/<chap>/summary.md` — narrative summary + a `## Core goals`
   section. The first bullet under `Core goals` becomes the player's
   on-screen "Your goal:" line at scene open
   (see `opening_scene.find_chapter_goal`).

### Step D — Author triggers

`triggers/<filename>.json` — declarative reactions over the event bus
(`scene.entered`, `combat.encounter_ended`, `intent.travel_resolved`,
…). Use this for:

* Scripted intros (`scene.entered` + `speak` action).
* Scripted combats (`scene.entered` + `start_encounter` action).
* Random-encounter tables on travel
  (`intent.travel_resolved` + `roll_random_encounter`).
* Arc advancement (`combat.encounter_ended` + `set_flag` /
  `publish_event`).

Trigger spec shape lives in `src/ai_dm/orchestration/triggers.py`
docstring; action verbs are listed in
`src/ai_dm/orchestration/trigger_actions.py::from_spec`.

### Step E — Validate

Run from repo root:

```bash
python tools/author/validate_pack.py <pack-root>
```

It checks:

* Every JSON file parses.
* `nodes.json` exits point at scene ids that exist somewhere.
* Every `feature.interactions[].grants` references a loot id declared
  in some `chapters/*/treasure.json`.
* Every `interactions[].starts_encounter` / `ends_encounter` /
  `victory.encounter_id` references an encounter id declared in
  `chapters/*/encounters.json`.
* Every NPC `default_anchor` matches an anchor id in the same
  location's `scene_locations.json`.
* Every `chapters/*/scenes.json` `location_id` matches a node id
  somewhere under `locations/`.
* Every trigger `do[].speak.text` is non-empty and every
  `start_encounter` references a known encounter id.

Fix all reported issues before moving on. Re-running the adapter
prompt with the validator output included makes the LLM self-correct
the file in place.

## 2. Field shapes (canonical reference)

These are the shapes the runtime expects. The adapter prompt
(`scenario_adapter_prompt.md`) embeds these as JSON Schema-like
templates so the LLM emits valid output the first time.

### 2.1. `features[].interactions[]`

```json
{
  "verb": "search_rubble",                  // snake_case action label
  "summary": "Search the rubble beside the altar.",
  "check": "wis.perception",                // optional: <ability>.<skill>
  "dc": 12,                                 // optional, required if check set
  "on_success": "Buried in the rubble: …",  // optional narration
  "on_failure": "You disturb only dust.",   // optional narration
  "narration": "Pews erupt …",              // free-form fallback narration
  "grants": ["loot.silver_tithe_bowl"],     // optional loot ids
  "starts_encounter": "encounter.x",        // optional combat hook
  "ends_encounter": "encounter.x",          // optional combat hook
  "ends_scene": true,                       // optional advance hook
  "requires_event": "combat.encounter_ended:encounter.x"  // gate visibility
}
```

A verb without a `check` is an automatic-resolution action (used for
hostile triggers like `break_altar`, `desecrate`, or pure narration
beats). Always provide `summary` so the player-facing affordance line
("At the Ruined Altar you can: …") is human-readable.

### 2.2. `nodes.json`

```json
{
  "nodes": [
    {
      "id": "ruined_chapel",
      "name": "Ruined Chapel",
      "description": "Player-facing prose. Must name every feature by its display name.",
      "exits": { "north": "cultist_cave", "south": "hofsaddel_pass" },
      "features": [
        {
          "id": "object.ruined_altar",
          "name": "Ruined Altar",
          "interactable": true,
          "tags": ["holy", "quest"],
          "description": "GM-and-LLM-facing prose. Must mention everything that interactions reference (e.g. 'rubble' if there is a search_rubble verb).",
          "interactions": [ /* see 2.1 */ ]
        }
      ]
    }
  ]
}
```

### 2.3. `scene_locations.json`

```json
[
  {
    "scene_id": "ruined_chapel",
    "anchors": [
      { "id": "anchor.chapel_door", "name": "chapel_door", "scene_id": "ruined_chapel",
        "x": 350, "y": 1500, "tags": ["entrance"] },
      { "id": "anchor.altar",       "name": "altar",        "scene_id": "ruined_chapel",
        "x": 1300, "y": 450,  "tags": ["holy", "quest"] }
    ],
    "zones": [
      { "id": "zone.nave", "name": "nave", "scene_id": "ruined_chapel",
        "shape": "rect", "rect": [250, 250, 1650, 1700], "tags": ["holy", "ruin"] }
    ]
  }
]
```

Anchor coordinates are in **Foundry pixels**; you can pick reasonable
numbers as long as they fall inside the map's known dimensions and
don't overlap each other heavily. The validator only checks references
and bounds, not aesthetics.

### 2.4. `chapters/<chap>/scenes.json`

```json
{
  "scenes": [
    {
      "id": "ruined_chapel",
      "name": "Ancient Devotees",
      "location_id": "stensia_road",
      "summary": "A collapsed chapel still hosts an undead service.",
      "time_of_day": "dusk",
      "starting_anchor": "anchor.chapel_door",
      "beats": [
        {
          "id": "beat.lay_dead_to_rest",
          "summary": "Destroy or release the skeletal worshippers.",
          "completes_on": {
            "event": "combat.encounter_ended",
            "encounter_id": "encounter.ancient_devotees"
          }
        }
      ]
    }
  ]
}
```

`completes_on.event` must be one the runtime publishes. Common ones:

* `scene.entered` — `{scene_id: …}`.
* `combat.encounter_ended` — `{encounter_id: …}`.
* `intent.resolved` — `{intent_type: "speak"|"interact"|"travel"|…, target_id: …}`.
* `intent.travel_resolved` — `{scene_id: …, label: …}`.
* `flag.set` — `{key: …, value: …}`.

### 2.5. `chapters/<chap>/encounters.json`

```json
[
  {
    "id": "encounter.ancient_devotees",
    "name": "Ancient Devotees",
    "scene_id": "ruined_chapel",
    "difficulty": "medium",
    "monsters": [
      { "name": "Skeleton Priest", "count": 1 },
      { "name": "Skeleton", "count": 3 }
    ],
    "victory": { "payload": { "encounter_id": "encounter.ancient_devotees" } }
  }
]
```

Monster `name` must resolve in `assets/srd5_2/monsters.json` (or a
pack-local monster file). If a creature in the scenario isn't in the
SRD, add it to `<pack>/monsters.json` first; do not silently rename
to a near-miss SRD entry.

### 2.6. `triggers/<file>.json`

```json
{
  "triggers": [
    {
      "id": "trg.army_intro_hofsaddel",
      "event": "scene.entered",
      "when": { "payload_eq": { "scene_id": "hofsaddel_pass" } },
      "do": [
        { "speak": { "text": "You crest Hofsaddel Pass …" } },
        { "set_flag": { "key": "army_intro_played", "value": true } }
      ],
      "once": true,
      "priority": 50
    }
  ]
}
```

### 2.7. `campaign.yaml`

Top-level manifest. Adapter only touches it on chapter additions
(append to `entrypoints` / `start`):

```yaml
id: armyofthedamned
name: Army of the Damned
entrypoints:
  first_chapter: chapter_01
start:
  scene: hofsaddel_pass
  player_character: pc_human
  party:
    - { id: companion_warrior, controller: ai, archetype: warrior, background: sellsword }
```

## 3. Iteration discipline

* **One chunk per pass.** Feed the adapter exactly one scene's worth
  of source text plus the relevant existing pack files (so it can
  reference ids that already exist). Don't ask it to re-generate the
  whole pack from scratch each time — diffs explode and ids drift.
* **Re-validate on every pass.** Pipe `validate_pack.py`'s output
  back into the next adapter call as "fix these specific issues" so
  the LLM corrects in place instead of rewriting unrelated files.
* **Diff-review before commit.** Adapter output is never committed
  blind. The reviewer's job is checking that the JSON faithfully
  reproduces the source scenario — not "does it look reasonable in
  isolation".
* **Author tests for irreversible content.** Boss encounters,
  campaign-ending triggers, and unique loot ids should have a smoke
  test under `tests/integration/` that loads the pack and asserts
  the encounter is reachable. The adapter cannot generate these
  tests — they're written by hand once.

## 4. What the adapter must NOT do

* Invent NPC names, place names, or treasure that isn't in the source.
* Choose DCs without a basis (use the source's stated DC; if absent,
  default to 12 for trivial, 14 for standard, 16 for hard, and flag
  it in the adapter's commit message so a reviewer rebalances).
* Edit `src/ai_dm/**`, `assets/srd5_2/**`, `config/**`, or any other
  engine code. The adapter is a **content-only** role.
* Combine multiple chapters' content into one file. One chapter per
  `chapters/<chap>/` directory, always.
* Drop authored content silently. If the source describes something
  the runtime can't currently express (a new mechanic, a custom
  spell), record it in `chapters/<chap>/summary.md` under a
  `## Unhandled` section so it can be revisited rather than lost.

