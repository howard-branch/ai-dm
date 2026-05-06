---
name: campaign-structure
description: On-disk layout and file schemas of an ai-dm campaign pack (`~/dnd/campaigns/<slug>/`) and its mutable runtime state under `data/campaigns/<slug>/`. Load this whenever you need to author, validate, modify, or programmatically read a campaign pack — `campaign.yaml`, chapters, locations, scenes, encounters, NPCs, treasure, triggers, world lore, prompts, characters seed, voices, portraits, maps, audio.
---

# Campaign pack structure

A **campaign pack** is a self-contained, *read-only*, distributable folder
that supplies all authored content for one campaign. The active pack is
chosen via `config/settings.yaml`:

```yaml
campaigns:
  root: ~/dnd/campaigns      # where packs live
  active: morgana            # slug under root, OR a path to a pack
  state_root: data/campaigns # where mutable state goes
```

Resolved by `ai_dm.campaign.pack.resolve_pack()` →
`CampaignPack { manifest, paths (read-only), state (writable) }`
(see `src/ai_dm/campaign/pack.py`). Shared 5e SRD lives in
`assets/rules/` + `assets/srd5_2/` and is **never** copied into a pack.

## Layout

```
~/dnd/campaigns/<slug>/        # READ-ONLY pack
  campaign.yaml                # manifest
  chapters/<chapter_id>/
    summary.md
    scenes.json
    encounters.json
    npcs.json
    treasure.json
  locations/<location_id>/
    description.md
    nodes.json                 # rooms / sub-locations + exits + features
    npcs.json                  # NPC placement + schedules
    scene_locations.json       # anchors + zones (Foundry coords)
    events.json                # ambient / scheduled flavour
  triggers/*.json              # declarative trigger DSL
  world/
    lore.md
    factions.json
    timeline.json
  prompts/
    system_dm.txt
    system_narrator.txt
    output_schema.json         # JSON-Schema for AI structured output
  characters/seed/             # starter PCs + companions/<id>.json
  voices.json                  # optional NPC voice map
  portraits/  maps/  audio/    # binary assets
  rules/                       # OPTIONAL pack-specific overlays merged
                               # over assets/rules/dnd5e_*.json catalogs

data/campaigns/<slug>/         # WRITABLE per-campaign state
  characters/   saves/         backups/   memory/   cache/
```

`CampaignPaths` keys are overridable in the manifest (`overrides:`) — see
`_DEFAULT_OVERRIDES` in `pack.py`. Don't hardcode paths; ask the
`CampaignPack` for them.

## `campaign.yaml` (manifest)

```yaml
id: morgana                     # slug; must match folder name
name: "The Morgana Chronicle"
version: 0.1.0
overrides: {}                   # {key: rel_path} to remap any of the
                                # default folders (chapters, locations, ...)
entrypoints:
  first_chapter: chapter_01
start:                          # hardcoded bootstrap (pack-specific)
  scene: candlekeep_courtyard   # scene_id from chapters/*/scenes.json
  player_character: morgana     # id from characters/seed/<id>.json
```

Loaded by `CampaignManifest.load()`. YAML preferred; JSON also accepted.

## `chapters/<chapter_id>/`

### `summary.md`
Free-form Markdown: chapter goals, tone, hooks. Read by the AI narrator
for context.

### `scenes.json`
```json
{
  "scenes": [
    {
      "id": "candlekeep_courtyard",
      "name": "The Courtyard at Dusk",
      "location_id": "candlekeep_courtyard",
      "summary": "...",
      "weather": "clear",
      "time_of_day": "dusk",
      "music": "candlekeep_dusk_ambient",
      "starting_anchor": "anchor.gate_inner",
      "beats": [
        {
          "id": "beat.greet_beren",
          "summary": "...",
          "completes_on": {
            "event": "intent.resolved",
            "intent_type": "speak",
            "target_id": "npc.beren"
          },
          "rewards": ["flag:greeted_beren"]
        }
      ]
    }
  ]
}
```
- `location_id` references a folder under `locations/`.
- `starting_anchor` references an id in that location's
  `scene_locations.json`.
- `beats[].completes_on` is matched against bus events by the planner.
- `rewards`: `flag:<key>` sets a flag; `thread:<id>` opens an arc thread.

### `encounters.json`
Top-level **list** (not wrapped in an object):
```json
[
  {
    "id": "encounter.hollow_echo",
    "name": "...",
    "scene_id": "candlekeep_courtyard",
    "trigger": "free-text human description",
    "difficulty": "hard",
    "xp": 450,
    "monsters": [
      {
        "id": "mon.hollow_echo",
        "name": "...", "type": "undead", "size": "medium",
        "ac": 13, "hp": 38, "speed": 30,
        "abilities": {"str":14,"dex":12,"con":14,"int":10,"wis":14,"cha":16},
        "saves": {"wis": 4, "cha": 5},
        "damage_resistances": ["necrotic"],
        "damage_immunities": ["poison"],
        "condition_immunities": ["charmed","frightened"],
        "senses": "darkvision 60 ft., passive Perception 12",
        "actions": [
          {"name":"Spectral Claw","to_hit":5,"reach":"5 ft.",
           "damage":"1d8+2 slashing + 1d6 necrotic"},
          {"name":"Echo Your Name","recharge":"5-6",
           "save":{"ability":"wis","dc":13},
           "effect":"..."}
        ],
        "tactics": "...",
        "loot_drop": "loot.echo_shard"
      }
    ],
    "victory": {
      "publish_event": "combat.encounter_ended",
      "payload": {"encounter_id": "encounter.hollow_echo"},
      "set_flags": {"echo_resolved": true}
    }
  }
]
```
Started programmatically via the `start_encounter` trigger action (see
below) which fires `combat.encounter_started`.

### `npcs.json`
Top-level list. **Chapter-scoped roleplay sheet** (personality, knows,
wants, fears, voice). Distinct from `locations/*/npcs.json` which only
places them in space.
```json
[
  {
    "id": "npc.beren",
    "name": "Old Beren",
    "role": "Gatekeeper of Candlekeep",
    "voice": "en-GB-RyanNeural",
    "appearance": "...",
    "personality": "...",
    "wants": "...",
    "fears": "...",
    "knows": ["fact 1", "fact 2"],
    "stats": {"ac": 11, "hp": 14, "passive_perception": 13}
  }
]
```

### `treasure.json`
```json
{
  "treasure": [
    {
      "id": "loot.echo_shard",
      "name": "Shard of the Hollow Echo",
      "rarity": "uncommon",          // common|uncommon|rare|very_rare|legendary
      "type": "wondrous",            // wondrous|weapon|armor|key|document|...
      "attunement": true,
      "description": "...",
      "weight": 0.1,
      "value_gp": 250,
      "tags": ["necromancy", "campaign-significant"]
    }
  ]
}
```
Referenced by `monsters[].loot_drop` and by trigger actions that grant
items.

## `locations/<location_id>/`

### `description.md`
Author-facing Markdown overview of the place. Fed to the narrator.

### `nodes.json` — sub-locations / rooms
```json
{
  "nodes": [
    {
      "id": "candlekeep_courtyard",
      "name": "Inner Courtyard",
      "description": "...",
      "exits": {"gate": "candlekeep_gate", "scriptorium": "candlekeep_scriptorium"},
      "features": [
        {"id":"object.sarcophagus","name":"Cracked Sarcophagus",
         "interactable":true,"tags":["necromancy","quest"]},
        {"id":"object.inner_door","name":"Barred Cloister Door",
         "interactable":true,"locked":true,"key":"loot.beren_key"}
      ]
    }
  ]
}
```
Loaded by `ai_dm.game.location_loader` /
`location_manager` / `location_service`.

### `npcs.json` — placement
```json
{
  "npcs": [
    {
      "id": "npc.beren",
      "name": "Old Beren",
      "default_anchor": "anchor.cloister_door",
      "scene_id": "candlekeep_courtyard",
      "disposition": "neutral|wary|curious|hostile|friendly",
      "schedule": [
        {"time":"dusk","anchor":"anchor.cloister_door"},
        {"time":"night","anchor":"anchor.gate_inner"}
      ],
      "escort": ["npc.tomeguard_a"]   // optional follower ids
    }
  ]
}
```
Roleplay data lives in the chapter `npcs.json`; the same `id` connects
them.

### `scene_locations.json` — anchors + zones (Foundry coords, pixels)
Top-level list, one entry per scene:
```json
[
  {
    "scene_id": "candlekeep_courtyard",
    "anchors": [
      {"id":"anchor.gate_inner","name":"gate_inner",
       "scene_id":"candlekeep_courtyard","x":300,"y":1800,
       "tags":["entrance","spawn:morgana"]}
    ],
    "zones": [
      {"id":"zone.exhumation","name":"exhumation",
       "scene_id":"candlekeep_courtyard","shape":"rect",
       "rect":[1800,700,2300,1100],
       "tags":["danger","quest","trigger:proximity_morgana"]}
    ]
  }
]
```
Coordinates are Foundry scene pixels. `spawn:<actor_id>` tag on an
anchor marks it as a spawn point for that actor.

### `events.json` — ambient / scheduled flavour
```json
{
  "events": [
    {"id":"ambient.raven_cry","scene_id":"candlekeep_courtyard",
     "kind":"ambient","every_seconds":45,
     "narration":"A raven cries once from the rookery."},
    {"id":"ambient.tomeguard_argument","scene_id":"candlekeep_courtyard",
     "kind":"ambient","trigger_after_seconds":15,"once":true,
     "narration":"..."},
    {"id":"ambient.sarcophagus_breath","scene_id":"candlekeep_courtyard",
     "kind":"ambient","when":{"flag_truthy":"noticed_sarcophagus"},
     "every_seconds":30,"narration":"..."}
  ]
}
```

## `triggers/*.json` — declarative trigger DSL

One file per scene (or grouping). Loaded by
`ai_dm.game.trigger_loader` and executed by
`ai_dm.orchestration.triggers` + `trigger_actions`.

```json
{
  "triggers": [
    {
      "id": "trg.echo_rises_on_interact",
      "event": "intent.resolved",            // bus topic to listen on
      "when": {                              // condition tree
        "all_of": [
          {"payload_eq": {"intent.target_id": "object.sarcophagus"}},
          {"not": {"flag_truthy": "echo_resolved"}}
        ]
      },
      "do": [                                // ordered actions
        {"speak": {"text": "The lid grinds. ..."}},
        {"start_encounter": {
          "encounter_id": "encounter.hollow_echo",
          "participants": [
            {"actor_id":"morgana","side":"players"},
            {"actor_id":"mon.hollow_echo","side":"enemies"}
          ]
        }}
      ],
      "once": true,
      "priority": 30
    }
  ]
}
```
Common building blocks:
- **Conditions**: `all_of`, `any_of`, `not`, `payload_eq`,
  `flag_truthy`. Dotted keys like `intent.target_id` traverse the
  payload.
- **Actions**: `speak`, `set_flag`, `publish_event`,
  `start_encounter` — and any others registered in
  `trigger_actions.py`. Check that file for the authoritative list
  before authoring new ones.
- **Events** are the same dotted topics used by `event_bus` —
  e.g. `scene.entered`, `intent.resolved`, `combat.encounter_ended`,
  `arc.thread_opened`, `chapter.advanced`.

## `world/`

- `lore.md` — long-form world bible; fed to the narrator.
- `factions.json`:
  ```json
  {"factions":[{
    "id":"faction.avowed","name":"The Avowed of Candlekeep",
    "alignment":"lawful neutral","agenda":"...",
    "disposition_to_<pc_id>":"guarded",
    "leaders":["The Keeper of Tomes (unseen)"],
    "tags":["library","host"]
  }]}
  ```
- `timeline.json`:
  ```json
  {"events":[{"year":-212,"title":"...","summary":"..."}]}
  ```

## `prompts/`

- `system_dm.txt` — system prompt for the DM/intent-router LLM call.
- `system_narrator.txt` — system prompt for the narrator LLM call.
- `output_schema.json` — JSON-Schema (`draft-07`) the narrator MUST
  conform to. Canonical fields: `narration` (req), `spoken_dialogue`,
  `dialogue[]` (`{npc_id, text, tone?, target?}`), `dice_requests[]`,
  `commands[]`, `state_updates[]`, `metadata{}`. Bump
  `schema_version` if you change shape.

## `characters/seed/`

Starter PC sheets copied into `data/campaigns/<slug>/characters/` on
first boot by `seed_characters()` (idempotent — won't overwrite live
state). Companions go under `characters/seed/companions/<id>.json`.
Schema is the same one used for live characters; minimum fields:
`id, name, race, class, level, abilities, ac, hp{current,max,temp},
speed, proficiency_bonus`. Optional rich fields: `subclass,
background, voice, portrait, appearance, personality, ideals, bonds,
flaws, saves, skills, senses, languages, spellcasting{...},
features[], inventory[], conditions[], notes`.

`spellcasting`:
```json
{"ability":"cha","save_dc":13,"attack_bonus":5,
 "slots":{"1":4,"2":2},
 "cantrips":["chill touch"],
 "spells_known":["cause fear","hex"]}
```

Familiar/companion entries add: `kind:"familiar"`, `bound_to:"<pc_id>"`,
optional `actions[]` mirroring the encounter-monster `actions` shape.

## `voices.json` (optional)

Map of `npc_id → voice_name` (edge-tts voices, e.g.
`en-GB-RyanNeural`). NPCs may also pin their voice inline in the
chapter `npcs.json`.

## Mutable state — `data/campaigns/<slug>/`

Managed exclusively by `ai_dm.persistence.campaign_store.CampaignStore`
(schema v3, auto-migrates v1/v2). **Never write JSON directly into
`saves/`.** `characters/` holds the live, mutated copies of the seed
PCs. `memory/` is owned by `ai_dm.memory.*`. `cache/` is disposable.

## Conventions / gotchas

- **IDs are stable strings** (`scene_id`, `npc.*`, `encounter.*`,
  `loot.*`, `anchor.*`, `zone.*`, `flag:*`, `thread:*`). Reuse the same
  id everywhere it appears — triggers, scenes, beats, and runtime
  events match by exact string.
- Some files use a top-level wrapper (`{"scenes":[...]}`,
  `{"nodes":[...]}`, `{"npcs":[...]}`, `{"events":[...]}`,
  `{"treasure":[...]}`, `{"factions":[...]}`, `{"triggers":[...]}`)
  while `chapters/*/encounters.json`, `chapters/*/npcs.json` and
  `locations/*/scene_locations.json` are bare top-level lists. Match
  the existing shape — the loaders are not always tolerant.
- **Coordinates** in `scene_locations.json` are Foundry scene pixels
  (origin top-left), not grid squares.
- **Flags** are pack-global booleans/strings keyed by short
  snake\_case names (e.g. `noticed_sarcophagus`); set by trigger
  actions / scene beat rewards and tested via `flag_truthy`.
- **Bus topics** the trigger DSL emits / listens on are the same
  dotted strings used elsewhere in the app (`scene.entered`,
  `intent.resolved`, `combat.encounter_started`,
  `combat.encounter_ended`, `arc.thread_opened`, `chapter.advanced`,
  `roll.resolved`, ...).
- **Pack-specific rules overlays** drop a JSON file with the same name
  as a shared catalog (`dnd5e_items.json`, `dnd5e_class_features.json`,
  ...) into `<pack>/rules/`; it is deep-merged on top by
  `ai_dm.app.catalog_loader.load_overlay`.
- A new pack is activated by editing one line in
  `config/settings.yaml`: `campaigns.active: <slug-or-path>`.

## Reference packs in this repo

- `~/dnd/campaigns/morgana/` — small, single-chapter; the canonical
  example. Read it first when authoring.
- `~/dnd/campaigns/ArmyOfTheDamned/` — multi-chapter (1–5), multi-
  location, with `voices.json`, `Maps/`, `Artwork/`, and a `README.md`.

