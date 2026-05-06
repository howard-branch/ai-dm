---
name: project-overview
description: High-level architecture and file/module map of the ai-dm project (AI Dungeon Master + Foundry VTT bridge in Python). Load this at the start of any task touching this repo to avoid re-scanning the tree. Re-read targeted files for deeper context as needed.
---

# AI Dungeon Master — project map

Hybrid AI + Foundry VTT Dungeon Master framework.

- **Python is authoritative** for state, rules, and orchestration.
- **Foundry VTT** is a thin rendering / execution layer (sheets, dice, tokens, scenes).
- **LLMs** produce only structured narrative / intent JSON — never free-form rule decisions.

Python ⇄ Foundry talk over a websocket relay (`ws_relay_server.py` + JS module in `foundry/`). Inbound Foundry events go through `foundry.authority.InboundArbiter` (self-echo / absorb / revert / ignore) before hitting the rest of the app.

## Top-level layout

```
ai-dm/
  assets/                 # shared, read-only D&D 5e SRD + house rules
    rules/                # dnd5e_*.json, house_rules.json, dnd5e_reference.md
    srd5_2/               # monsters/spells/progression JSON + raw + core
  campaigns/              # (legacy) sample packs — real packs live at ~/dnd/campaigns/<slug>/
  config/                 # logging.yaml, models.yaml, settings.yaml
  data/                   # MUTABLE runtime state
    campaigns/<slug>/     # characters/, saves/, memory/, cache/  (per active campaign)
    cache/  memory/  saves/
  foundry/
    module/               # Foundry VTT module (module.json, scripts/, templates/, styles/, assets/)
    payload_examples/     # canonical JSON for every command type (give_item, roll_check, ...)
  scripts/                # dev runners: run_dev.sh, reset_state.sh, send_test_command*.py,
                          #   run_foundry_replay.py, sync_foundry_assets.py, validate_assets.py,
                          #   import_foundry_content.py, export_save.py
  src/ai_dm/              # main Python package (see module map below)
  tests/
    unit/  integration/  fixtures/
  tools/                  # SRD scrapers/parsers (parse_srd_*.py, sdr_scraper.py, author/)
  pyproject.toml  README.md  LICENSE  disclaimer
```

Entry point: `python -m ai_dm.main` (see `src/ai_dm/main.py`).

## `src/ai_dm/` module map

### `app/` — bootstrap / DI / runtime
`bootstrap.py`, `container.py`, `runtime.py`, `settings.py`, `lobby.py`,
`opening_scene.py`, `scene_setup.py`, `character_wizard.py`,
`character_equipment.py`, `character_features.py`, `character_spells.py`,
`catalog_loader.py`, `transcript_logger.py`.

### `orchestration/` — event bus + dispatchers + turn flow
- `event_bus.py` — pub/sub spine (`bus.publish(topic, payload)` / `bus.subscribe`).
- `command_router.py` — outbound command dispatch to Foundry; has `pre_dispatch_hooks` (echo suppression lives here).
- `director.py` — top-level loop / scene director.
- `turn_manager.py`, `actor_session.py`, `conditions.py`.
- Dispatchers: `player_input_dispatcher.py`, `structured_intent_dispatcher.py`,
  `roll_request_dispatcher.py`, `interaction_effects.py` (authored grants / encounter / scene hooks on `roll.resolved`).
- `triggers.py` + `trigger_actions.py` — declarative trigger DSL loaded from the active pack.
- `xp_collector.py`, `xp_awarder.py`, `remote_character_wizard.py`.

### `rules/` — D&D 5e rules engine (seedable, deterministic)
Dice & checks: `dice.py`, `dc.py`, `skill_checks.py`, `abilities.py`, `dm_rolls.py`.
Combat: `attack.py`, `damage.py`, `combat_service.py`, `initiative.py`, `turn.py`,
`opportunity_attack.py`, `grapple.py`, `cover.py`, `targeting.py`, `areas_of_effect.py`,
`weapon_mastery.py`, `weapons.py`, `armor.py`, `equipment.py`, `adventuring_gear.py`,
`magic_items.py`, `attunement.py`, `tools.py`.
Status: `conditions.py`, `condition_predicates.py`, `concentration.py`,
`exhaustion.py`, `death_saves.py`, `rests.py`.
World/movement: `movement.py`, `movement_time.py`, `travel.py`, `mounts.py`,
`stealth.py`, `light.py`, `hazards.py`, `traps.py`, `encumbrance.py`,
`sustenance.py`, `currency.py`.
Spells & progression: `spell_resolver.py`, `spell_progression.py`,
`xp_budget.py`, `action_resolver.py`, `actions.py`, `engine.py`,
`validators.py`, `house_rules.py`, `srd_core.py`, `dnd5e_reference.py`,
`glossary/`.

### `ai/` — LLM I/O (strict schemas, no rule decisions)
`client.py` (`AIClient`), `prompts.py`, `context_builder.py`,
`narrator.py`, `npc_dialogue.py`, `planner.py` (chapters/scenes/beats + Timeline),
`intent_parser.py`, `intent_router.py`, `intent_schemas.py`,
`schemas.py`, `arc_schemas.py`, `response_parser.py`.

### `foundry/` — bridge to Foundry VTT
- `client.py`, `socket_bridge.py`, `ws_relay_server.py`, `relay_supervisor.py`.
- `command_queue.py`, `batch_executor.py`, `protocol.py`, `payloads.py`,
  `results.py`, `errors.py`, `validator.py`, `registry.py`.
- `authority.py` — `InboundArbiter` + `EchoSuppressor` (self_echo / absorb / revert / ignore). Re-publishes as `authority.*` events.
- `journal.py` — `JournalService` (create/append journal entries).
- `reconciler.py` — diff + auto-heal HP/name drift.
- `snapshots.py`, `sync_service.py`.

### `game/` — runtime world state (in-memory + persisted via `CampaignStore`)
`session_manager.py`, `campaign_loader.py`, `location_loader.py`,
`location_manager.py`, `location_model.py`, `location_service.py`,
`scene_state.py`, `encounter_manager.py`, `combat_machine.py`,
`combat_state.py`, `combatant_state.py`, `monster_state.py`,
`npc_manager.py`, `party_state.py`, `inventory_manager.py`,
`condition_tracker.py`, `condition_instance.py`, `quest_manager.py`,
`spell_state.py`, `timeline.py`, `travel_state.py`, `clock.py`,
`state_store.py`, `trigger_loader.py`.

### `persistence/` — atomic, locked, versioned save files
`campaign_store.py` (schema v3, auto-migrates v1/v2), `json_store.py`,
`atomic_writer.py`, `file_lock.py`, `backups.py`, `roll_log.py`.

### `memory/` — long/short term campaign memory
`campaign_memory.py`, `npc_memory.py`, `relationships.py`,
`retrieval.py`, `short_term.py`, `summariser.py`.

### `audio/` — TTS in, mic + STT out
`tts.py` (edge-tts → piper fallback), `voices.py`, `audio_queue.py`,
`narration_dispatcher.py`, `playback.py`, `mic_recorder.py`,
`speech_input.py`, `transcribe.py`, `voice_input_pump.py`, `wakeword.py`.

### `services/` — high-level read/write helpers
`save_service.py`, `chapter_service.py`, `recap_service.py`, `transcript_service.py`.

### `models/` — typed payloads / domain types
`commands.py` (outbound command dataclasses — `give_item`, `activate_scene`, ...),
`ai_output.py`, `domain.py`, `state.py`.

### `ui/` — Qt desktop UI
`main_window.py`, `panels/`, `widgets/`, `viewmodels/`.

### `utils/`
`paths.py`, `logging.py`, `dotenv.py`, `time.py`.

### `campaign/`
`pack.py` — `CampaignPack` loader.

## Key conventions

- **Event topics** are dotted strings: `roll.resolved`, `scene.entered`,
  `narrator.output_ready`, `foundry.token_moved`, `authority.actor_updated`, ...
- **Outbound writes** must go through `CommandRouter.dispatch([...commands])` so
  they get fingerprinted into the `EchoSuppressor`.
- **Persistence** goes through `CampaignStore` (never write JSON directly to
  `data/campaigns/<slug>/saves/`).
- **Campaign packs** live at `~/dnd/campaigns/<slug>/` (or any path set in
  `config/settings.yaml :: campaigns.active`). Packs are read-only and contain
  `campaign.yaml`, `chapters/`, `locations/`, `prompts/`, `triggers/`,
  `world/`, `voices.json`, `characters/seed/`, `portraits/`, `maps/`, `audio/`.
  Mutable state lives in `data/campaigns/<slug>/`.
- **Shared 5e content** stays in `assets/rules/` and `assets/srd5_2/` — never
  copied into a pack.
- **Tests**: `tests/unit/` (fast, mock the bus / router / managers — see
  `tests/unit/test_interaction_effects.py` for the canonical pattern) and
  `tests/integration/`. Run with `pytest`.

## When to read more

This file is the cheap index. For real work, open the specific module(s) under
`src/ai_dm/` you're touching — the per-file names above tell you where to look.

