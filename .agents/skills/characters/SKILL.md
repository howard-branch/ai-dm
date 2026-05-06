---
name: characters
description: How player characters (PCs), AI-controlled companions, NPCs, and monster combatants are authored, created, persisted, and mutated in ai-dm. Load this whenever a task touches character sheets, the character-creation wizard, party composition, NPC roleplay vs. placement, monster stat blocks, or the runtime CombatantState. Covers the seed → wizard → live-sheet → CombatantState pipeline and the file/module map for every layer.
---

# Characters — PCs, companions, NPCs, monsters

ai-dm has **four** character-shaped concepts. Don't conflate them:

| Kind | Authored in pack? | Live state? | Has `CombatantState`? | Controller |
|---|---|---|---|---|
| **PC** (player character) | seed sheet OR wizard-built | yes (`state.characters/<id>.json`) | when in encounter | `player` |
| **Companion** (AI-controlled party member) | seed sheet OR built from manifest hints | yes (same dir) | when in encounter | `ai`, `source="pc"` |
| **NPC** (named, roleplayed) | chapter `npcs.json` (sheet) + location `npcs.json` (placement) | npc_memory + relationships | only if spawned into combat | `ai`, `source="npc"` |
| **Monster / mob** | SRD `assets/srd5_2/monsters.json` OR encounter `monsters[]` inline | none until spawned | yes (hydrated from a `StatBlock`) | `ai`, `source="npc"` |

The single source of truth for what a creature *can do at this exact moment in this fight* is always `CombatantState` (`src/ai_dm/game/combatant_state.py`). PC sheets and monster `StatBlock`s are *inputs* that get hydrated into one.

---

## 1. Where character data lives

### Pack (read-only, distributable)
`~/dnd/campaigns/<slug>/`
- `characters/seed/<pc_id>.json` — PC starter sheet. Copied to live state on first boot by `seed_characters()` (idempotent — never overwrites).
- `characters/seed/companions/<id>.json` — companion / familiar starter sheet, same schema as a PC sheet plus `kind: "familiar"`, `bound_to: "<pc_id>"`, optional `actions[]` mirroring a monster stat block.
- `chapters/<id>/npcs.json` — **roleplay sheet** for NPCs (personality, knows, wants, fears, voice, mini stats).
- `locations/<id>/npcs.json` — **placement** of those same `npc.*` ids (default anchor, scene, disposition, schedule, escort).

### Live runtime state (writable, per active campaign)
`data/campaigns/<slug>/`
- `characters/<id>.json` — live PC and companion sheets. Mutated in place. **Authoritative.**
- `saves/save.json` — `CampaignSnapshot` written by `CampaignStore` (includes `party`, `actor_state`, `npc_memory`, `relationships`, `combat`).
- `memory/` — `NPCMemoryStore` per-NPC event logs (`memory.npc_max_events` cap from settings).
- `backups/`, `logs/`.

**Never** write into `pack.paths.characters_seed` or directly into `saves/`. Ask `CampaignPack` for paths; ask `CampaignStore` to persist runtime state.

### Shared SRD catalogs (read-only, repo-resident)
`assets/srd5_2/monsters.json`, `spells.json`, `progression.json`; `assets/rules/dnd5e_*.json`. Pack-specific overlays drop a same-named JSON file into `<pack>/rules/` and are deep-merged by `ai_dm.app.catalog_loader.load_overlay`.

---

## 2. PC sheet schema (live + seed are identical)

Required minimum: `id, name, class, level, abilities{str,dex,con,int,wis,cha}, ac, hp{current,max,temp}, speed, proficiency_bonus`.

Built by `build_sheet()` (pure) in `src/ai_dm/app/character_wizard.py`. Canonical fields:

```jsonc
{
  "id": "morgana",
  "name": "Morgana",
  "pronouns": "she/her",
  "race": "Half-Elf",
  "class": "Witch (Necromancer)",        // human label; lookup is by Archetype.label
  "subclass": "Pact of the Bone",
  "level": 3,
  "background": "Exile",
  "alignment": "neutral",
  "voice": "en-GB-SoniaNeural",
  "portrait": "portraits/morgana.png",
  "appearance": "...", "personality": "...",
  "ideals": [...], "bonds": [...], "flaws": [...],

  "abilities":   {"str":8,"dex":14,"con":14,"int":13,"wis":12,"cha":17},
  "ability_mods": {...},                  // derived; cached
  "saves" | "saving_throws": {...},       // derived; cached
  "saving_throw_profs": ["wis","cha"],
  "skills": {"arcana":3, ...},

  "ac": 13,
  "hp":  {"current":24,"max":24,"temp":0},
  "speed": 30,
  "proficiency_bonus": 2,
  "senses":   {"darkvision":60,"passive_perception":11},
  "languages":["Common","Elvish","Infernal"],

  "spellcasting": {                        // null for non-casters
    "ability":"cha","save_dc":13,"attack_bonus":5,
    "slots":{"1":4,"2":2},
    "cantrips":["chill touch","..."],
    "spells_known":["cause fear","hex"]    // legacy v1 shape
  },
  "spells": {                              // v2 shape; set by build_sheet
    "casting_style":"known"|"prepared",
    "cantrips":[...], "known":[...], "prepared":[...], "spellbook":[...]
  },

  "features":          [{"name":"...","summary":"..."}],
  "abilities_features":[...],              // structured class+bg features
  "inventory":         [{"id":"item.dagger","name":"...","qty":1,"equipped":true}],
  "currency":          {"gp":17,"sp":0,"cp":0,"ep":0,"pp":0},

  "conditions":[], "resistances":[], "vulnerabilities":[], "immunities":[],
  "exhaustion": 0,
  "death_saves": {"successes":0,"failures":0,"stable":false,"dead":false},

  "controller": "player" | "ai",          // companions get "ai"
  "kind":      "familiar",                 // companions/familiars only
  "bound_to":  "<pc_id>",                  // companions/familiars only

  "notes": "..."
}
```

Sheet I/O: `sheet_path(pack, pc_id)` → `pack.state.characters/<id>.json`. Read with `_load_character_sheet()` in `bootstrap.py` (transparently migrates legacy v1 spell blocks via `_migrate_spell_block()`). Write atomically with `write_sheet()` → `atomic_write_json()`.

---

## 3. The character-creation wizard

`src/ai_dm/app/character_wizard.py` (≈770 LOC). Five steps: **Name → Archetype → Stats (auto + 1 optional bump) → Background → Confirm**. Stdlib only (`input`/`print`).

### Public surface
- `ARCHETYPES: dict[str, Archetype]` — class options (key, label, emoji, blurb, abilities, AC, HP, features, optional `class_key` + `casting_style` for casters, `saving_throw_profs`).
- `BACKGROUNDS: dict[str, Background]` — `exiled_noble, forbidden_scholar, wandering_mage, hedge_witch, sellsword, ...`.
- `build_sheet(pc_id, name, archetype_key, stat_bump, background_key, *, purchases=None, cantrip_picks=None, spell_picks=None, pack=None, level=1) -> dict` — **pure** sheet builder. Use this from any code path that needs to mint a sheet (companions, tests, scripted PCs).
- `run_wizard(pc_id=None, *, input_fn=input, print_fn=print, pack=None) -> dict` — interactive 5-step IO loop. Returns the built sheet (caller writes it).
- `needs_wizard(pack, pc_id) -> bool` — True only when `pc_id` is set AND no live sheet AND no seed sheet exists. **A seed file in the pack always suppresses the wizard.**
- `sheet_path(pack, pc_id)`, `write_sheet(pack, pc_id, sheet)`.
- `apply_level(sheet, new_level) -> dict` — re-derives `level`, `proficiency_bonus`, `spellcasting` (cantrip/spell caps + slot dict), and slot maxes from `ai_dm.rules.spell_progression`. **Never forgets** existing learned cantrips/spells.
- `validate_response(payload) -> list[str]` — used by the remote wizard.
- `_spellcasting_for(arch, level)` — internal SRD-driven slot/cap derivation.

### Helpers
- `ai_dm.app.character_equipment` — `apply_kit(archetype_key, pack=...)`, `apply_purchases(...)`, `load_items_catalog(pack=...)`. Honors pack overlays.
- `ai_dm.app.character_features` — `features_for(archetype_key, background_key, pack=...)`.
- `ai_dm.app.character_spells` — `available_spells(...)`, `pick_starting_spells(...)`, `prepare_spells(...)`. Enforces caps on **new** picks only.
- `ai_dm.rules.spell_progression` — `proficiency_bonus_for(class_key, level)`, `spellcasting_block(class_key, level, ability_mod)`. Aliases: `scholar→wizard`, `witch→warlock` (see `_CLASS_ALIASES`).
- `ai_dm.rules.abilities` — `ability_mods()`, `saving_throw_mod()`.

### Remote wizard (Foundry)
`src/ai_dm/orchestration/remote_character_wizard.py` — when Foundry is connected, sends `wizard_request` over the relay, awaits `foundry.wizard_response`, validates with `validate_response`, then calls `build_sheet`. The Foundry module renders it as a Dialog. Local-only runs use `run_wizard()` over stdin.

### Bootstrap gate
`src/ai_dm/app/bootstrap.py:_maybe_run_character_wizard()`:
1. If `AI_DM_NEW_CHARACTER=1` (set by `--new-character` CLI flag) → force the wizard.
2. Otherwise resolve `pc_id` from `manifest.start.player_character`.
3. If `needs_wizard(pack, pc_id)` → `run_wizard(pc_id, pack=pack)` → `write_sheet(...)`.
4. Else: leave the existing sheet alone.

**Consequence for pack authors:** to *enable* the wizard for your pack, omit `characters/seed/<pc_id>.json`. To *bypass* it, ship a seed file.

---

## 4. Party / companions

The party is declared in `campaign.yaml` under `start.party`:

```yaml
start:
  scene: stone_chamber
  player_character: hero
  party:                              # optional; omit for solo packs
    - id: hero                        # str shorthand → controller "ai"
    - id: brann
      name: "Brann the Bold"
      controller: ai                  # ai | player
      archetype: warrior              # one of ARCHETYPES keys
      background: sellsword           # one of BACKGROUNDS keys
```

`bootstrap._seed_ai_companions(pack)` walks every AI-controlled member and ensures a live sheet exists, in this order:

1. Live sheet present → leave it.
2. Seed file at `characters/seed/<id>.json` → `seed_characters()` will copy it (do nothing now).
3. Otherwise → `build_sheet(cid, name, archetype, None, background, pack=pack)`, tag `controller: "ai"`, `write_sheet`.

Runtime party state is `PartyState` (`src/ai_dm/game/party_state.py`, Pydantic v2): `members[]`, `xp_pool`, `levels`, `pending_xp`, `xp_log[]`. Persisted under `CampaignSnapshot.party`. XP is awarded by `ai_dm.rules.xp_budget.award_xp` and applied via `record_kill` / `finalize_encounter` (driven by `EncounterState` and `XPCollector`).

---

## 5. NPCs — two files, one id

NPCs are split deliberately:

- **Chapter `npcs.json`** (`chapters/<id>/npcs.json`, top-level **list**) — the *roleplay sheet*: `id, name, role, voice, appearance, personality, wants, fears, knows[], stats{ac,hp,passive_perception}`. Read by the narrator and the AI dialogue loop.
- **Location `npcs.json`** (`locations/<id>/npcs.json`, wrapped `{"npcs":[...]}`) — the *placement*: `id, name, default_anchor, scene_id, disposition, schedule[], escort[]`. Read by `npc_manager`/`location_service` to put tokens on the map.

The two files **must** share `id`. Add a chapter entry without a placement and the NPC has no body; add placement without a sheet and the narrator has no personality.

### Runtime
- `src/ai_dm/game/npc_manager.py — NPCManager`:
  - `register_stat_block(block | dict) -> StatBlock` and `register_many(...)` — load the immutable stat-block library (SRD monsters + inline encounter monsters).
  - `get_stat_block(key)`, `stat_blocks()`.
  - `spawn(stat_block_key, *, actor_id, ...)` → hydrates a `CombatantState` and tracks it in `_spawned`.
- `src/ai_dm/memory/npc_memory.py — NPCMemoryStore` — per-`npc_id` short-event log. Persisted in `CampaignSnapshot.npc_memory`. Capped by `settings.memory.npc_max_events` (default 50).
- `src/ai_dm/memory/relationships.py — RelationshipMatrix` — pairwise dispositions (NPC↔NPC, NPC↔PC). Persisted in `CampaignSnapshot.relationships`.
- Voices: `voices.json` at pack root maps `npc_id → edge-tts voice name`. Inline `voice` on the chapter NPC sheet wins.

---

## 6. Monsters and stat blocks

`src/ai_dm/game/monster_state.py — StatBlock` (Pydantic v2). Composed of `Senses`, `Trait`, `MonsterAttack`, `MonsterAction`, `MonsterReaction`, `LegendaryAction`. Plus `cr_to_xp(cr)`.

Sources:
- **SRD library** — `assets/srd5_2/monsters.json` is registered into `NPCManager` at startup.
- **Encounter inlines** — `chapters/*/encounters.json` `monsters[]` entries (see the campaign-structure skill for the schema). Inline monsters get their own `id` (`mon.<slug>`) and may set `loot_drop` referencing a `treasure[].id`.

Encounters are launched via the `start_encounter` trigger action (see `triggers/`); that publishes `combat.encounter_started` and the `EncounterManager` spawns each `participants[]` entry through `NPCManager.spawn(...)`.

---

## 7. CombatantState — the runtime authority

`src/ai_dm/game/combatant_state.py — CombatantState` (Pydantic v2, `extra="forbid"`). Every PC, companion, NPC, or monster currently *in an encounter* exists here. PC sheets and `StatBlock`s are *inputs*; this is the authority.

Identity / classification:
- `actor_id`, `token_id`, `name`
- `team: Team` (players / enemies / neutral / allies)
- `controller: Controller` (`player | ai`)
- `source: Source` (`pc | npc`) — drives which input pipeline (sheet vs. stat block) hydrated it.

Vitals: `hp, max_hp, temp_hp, ac`. Turn order: `initiative, initiative_bonus`. Geometry: `position, speed`. Status: `conditions[ConditionInstance], concentration`. Damage modifiers: `resistances, vulnerabilities, immunities, condition_immunities`.

Monster surface (populated when `source == "npc"`): `senses, languages, challenge_rating, xp, traits, actions, reactions, legendary_actions, legendary_actions_per_round, legendary_actions_remaining, stat_block_key`.

Survival: `exhaustion`, `death_saves`. Derived caches: `ability_mods`, `saving_throws`. Resources: `resources: dict[str, ResourceUse]`, `spell_slots: dict[int, SpellSlot]`.

Spells (ids only; full records stay on the sheet):
- `cantrips[]` — always-on, no slot cost.
- `known_spells[]` — leveled spells learned. For `casting_style="known"` (sorcerer/bard/warlock family) this is what's castable.
- `prepared_spells[]` — for `casting_style="prepared"` (cleric/druid/wizard/paladin), the daily castable subset; capped by `prepared_cap`.
- `rituals[]` — ritual-castable subset; gated by `ritual_caster`.
- Caster derived: `spellcasting_class, spellcasting_ability, spell_attack_bonus, spell_save_dc, casting_style, prepared_cap, ritual_caster`.

Action economy: `action_used, bonus_action_used, reaction_used, movement_used`. Transient (cleared on `start_of_turn`): `dashed, dodging, disengaging, helping_target, readied_action`. `hidden` persists until broken.

Inventory uses `Inventory` / `EquipmentSlots` / `CarriedItem` / `Coins` (also in this file). Helpers near the bottom: `_opt_str/_opt_int/_opt_casting_style`, `_spell_ids`, `_slots_from_sheet`, `_resources_from_features`.

**Mutate combatants only through `CombatantState` (or its helper methods).** Foundry is a projection target; diff against the previous projection to sync.

---

## 8. End-to-end pipeline

```
campaign.yaml
   start.player_character ─────────────────────────┐
   start.party             ─────────────────────┐  │
                                                 │  │
characters/seed/<id>.json ──┐                    │  │
                            │                    │  │
   bootstrap                │                    │  │
   _maybe_run_character_wizard:                  │  │
       needs_wizard? ── no seed & no live ──────────┘
            │
            ├── run_wizard()  →  build_sheet()  →  write_sheet()
            └── (seed exists) →  seed_characters()  copies into state.characters/

   _seed_ai_companions(pack):
       for m in start.party where controller=="ai":
           live? skip.  seed? skip (copied later).  else build_sheet+tag ai+write.

   apply_hardcoded_start():  activate scene, ensure PC, spawn token at starting_anchor.

   At encounter start:
       SRD monsters + encounter inlines → NPCManager.register_stat_block(...)
       trigger action start_encounter → EncounterManager spawns each participant
           PC/companion: hydrate CombatantState from <id>.json (source="pc")
           NPC/monster: NPCManager.spawn(stat_block_key, actor_id=...)
                        (source="npc", deep-copy actions/reactions from StatBlock)

   At encounter end:
       PartyState.record_kill(...) per defeated foe
       PartyState.finalize_encounter() → award XP, update levels
       CampaignStore.save() → snapshot {party, actor_state, npc_memory, ...}
```

---

## 9. Authoring checklist

**Adding a PC seed sheet to a pack** — drop `characters/seed/<id>.json`. The wizard will be skipped for that id. Use `build_sheet(...)` in a script if you want a deterministic baseline.

**Enabling the wizard for a pack** — *don't* ship `characters/seed/<player_character>.json`. The first launch will run `run_wizard()` and write the result into `data/campaigns/<slug>/characters/`.

**Adding an AI companion** — add to `start.party` with `controller: ai` and either `archetype` + `background` hints (auto-generated) or a `characters/seed/companions/<id>.json` (hand-crafted). Companion sheets follow the PC schema plus `kind`, `bound_to`, optional `actions[]`.

**Adding an NPC** — write **two** entries with the same `id`:
1. Chapter `npcs.json` (roleplay): personality, knows, wants, fears, voice, mini stats.
2. Location `npcs.json` (placement): default anchor, scene, disposition, optional schedule/escort.

Optional: pin a voice in `voices.json` (overridden by inline `voice` on the chapter sheet).

**Adding a monster** — either:
- *SRD-stock*: reference an existing `mon.*` key from `assets/srd5_2/monsters.json` in `encounter.participants`.
- *Custom inline*: add a `monsters[]` entry to `chapters/*/encounters.json` with full stat-block fields (see campaign-structure skill §`encounters.json`). Use a stable `id` like `mon.<slug>`. Reference loot via `loot_drop`.

**Levelling up** — call `apply_level(sheet, new_level)` then `write_sheet(pack, pc_id, sheet)`. Don't hand-edit `proficiency_bonus` or slots.

**Mutating in combat** — go through `CombatantState` (HP, conditions, concentration, slots, action economy). The sheet is updated at rest/save points, not every tick.

---

## 10. Gotchas

- The chapter NPC `npcs.json` is a **bare top-level list**; the location version is **wrapped** `{"npcs":[...]}`. Loaders are not always tolerant — match the existing shape.
- `needs_wizard` returns False if a seed file exists. Deleting only the live sheet at `state.characters/<id>.json` will *not* re-run the wizard if a seed is present.
- `Archetype.label` (not `key`) is what gets written into `sheet["class"]`. `_archetype_for_sheet` looks up by lowercase label, falling back to key.
- `casting_style` controls whether `known_spells` or `prepared_spells` are castable on `CombatantState`. `build_sheet` derives it from the archetype; `_migrate_spell_block` retro-fits legacy v1 sheets.
- PC and companion sheets share the same schema and the same directory; the only differentiator is `controller` and the optional `kind`/`bound_to`. Filtering on `kind == "familiar"` is the cleanest way to find companions.
- `CombatantState` has `extra="forbid"`. Any field you add must be added to the model — never stash data in `model_dump()` after the fact.
- Pack-specific class/background overlays go in `<pack>/rules/` (deep-merged catalogs); the wizard reads them via `pack=` kwarg on `apply_kit` / `available_spells` / `features_for`.

---

## 11. File map (quick lookup)

| File | What it owns |
|---|---|
| `src/ai_dm/app/character_wizard.py` | `Archetype`, `Background`, `ARCHETYPES`, `BACKGROUNDS`, `build_sheet`, `run_wizard`, `needs_wizard`, `sheet_path`, `write_sheet`, `apply_level`, `validate_response`, `_spellcasting_for` |
| `src/ai_dm/app/character_equipment.py` | `apply_kit`, `apply_purchases`, `load_items_catalog` |
| `src/ai_dm/app/character_features.py` | `features_for` |
| `src/ai_dm/app/character_spells.py` | `available_spells`, `pick_starting_spells`, `prepare_spells` |
| `src/ai_dm/app/bootstrap.py` | `_maybe_run_character_wizard`, `_seed_ai_companions`, `_party_members`, `_load_character_sheet`, `_migrate_spell_block`, `_apply_hardcoded_start` |
| `src/ai_dm/orchestration/remote_character_wizard.py` | Foundry-side wizard (`wizard_request` / `foundry.wizard_response`) |
| `src/ai_dm/campaign/pack.py` | `CampaignPack`, `CampaignPaths.characters_seed`, `seed_characters` |
| `src/ai_dm/game/combatant_state.py` | `CombatantState`, `Inventory`, `EquipmentSlots`, `CarriedItem`, `Coins`, `SpellSlot`, `ResourceUse`, `Concentration`, `Position` |
| `src/ai_dm/game/monster_state.py` | `StatBlock`, `Senses`, `Trait`, `MonsterAttack`, `MonsterAction`, `MonsterReaction`, `LegendaryAction`, `cr_to_xp` |
| `src/ai_dm/game/npc_manager.py` | `NPCManager` (stat-block library + spawned-NPC registry) |
| `src/ai_dm/game/party_state.py` | `PartyState`, `XPAward` |
| `src/ai_dm/game/encounter_manager.py` | `EncounterState`, encounter spawn / lifecycle |
| `src/ai_dm/memory/npc_memory.py` | `NPCMemoryStore` |
| `src/ai_dm/memory/relationships.py` | `RelationshipMatrix` |
| `src/ai_dm/persistence/campaign_store.py` | `CampaignStore`, `CampaignSnapshot` (`party`, `actor_state`, `npc_memory`, `relationships`, `combat`) |
| `src/ai_dm/rules/spell_progression.py` | `proficiency_bonus_for`, `spellcasting_block`, `_CLASS_ALIASES` |
| `src/ai_dm/rules/abilities.py` | `ability_mods`, `saving_throw_mod` |
| `src/ai_dm/rules/xp_budget.py` | `award_xp`, `level_for_xp` |

