![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg)

# SRD 5.2.1 Core Catalog

This directory is the **single source of truth** for SRD 5.2.1
mechanical primitives shared between the Python rules engine
(`src/ai_dm/rules/`) and the Foundry-side mirror
(`foundry/module/scripts/srd/`).

> Disclaimer: SRD content (CC-BY) — see repo-root `disclaimer`.

## Sync model

```
assets/srd5_2/core/*.json           ← canonical, edit here
        │
        │  scripts/sync_foundry_assets.py  (byte copy)
        ▼
foundry/module/assets/srd5_2/core/  ← mirror, do NOT hand-edit
```

`scripts/validate_assets.py` enforces:

1. JSON parses
2. Each mechanics file matches its hard-coded shape contract
3. The Foundry mirror is byte-equal
4. Per-class PB in `assets/srd5_2/progression.json` agrees with
   `core/proficiency.json["by_level"]`

The contract test
`tests/integration/test_srd_python_foundry_sync.py` re-verifies the
same invariants from the Python side and additionally greps the JS
mirror for parity on `ABILITIES`.

## Mechanics files (consumed at runtime)

| File | Python owner | JS owner | Notes |
|---|---|---|---|
| `abilities.json` | `ai_dm.rules.abilities` | `scripts/srd/abilities.js` | 6 SRD ability keys + score range |
| `proficiency.json` | `ai_dm.rules.dnd5e_reference.proficiency_for_level` | `scripts/srd/dice.js` | `by_level[20]` SRD curve |
| `dcs.json` | `ai_dm.rules.dc` | `scripts/srd/dc.js` | named DCs + spell-save / spell-attack formulas |
| `damage_types.json` | `ai_dm.rules.damage` | `scripts/srd/damage.js` | 13 damage types |
| `conditions.json` | `ai_dm.rules.conditions` | `scripts/srd/conditions.js` | 14 conditions + exhaustion (15 total) |
| `exhaustion.json` | `ai_dm.rules.exhaustion` | `scripts/srd/exhaustion.js` | one-track 0–6, −2 d20 / −5 ft per level |
| `death_saves.json` | `ai_dm.rules.death_saves` | `scripts/srd/death_saves.js` | DC 10, 3 successes / 3 failures, nat-20 → 1 HP |

All of the above are loaded via the cached `srd_core.load(name)`
helper on the Python side, and via `loadSrdCore()` on the JS side.

## Catalog files (reserved; not yet wired)

These describe content (species, classes, feats, …) rather than
core mechanics. They are NOT consumed by `srd_core.load(...)` callers
today; future sheet-builder / wizard layers may load them.

| File | Status |
|---|---|
| `backgrounds.json` | catalog — reserved |
| `classes.json` | catalog — reserved |
| `subclasses.json` | catalog — reserved |
| `species.json` | catalog — reserved |
| `feats.json` | catalog — reserved |
| `hit_dice.json` | catalog — reserved |
| `proficiencies.json` | catalog — reserved (tools/armor/weapons) |
| `skills.json` | catalog — reserved |

> **Editing rules**: never edit a file under
> `foundry/module/assets/srd5_2/core/` directly. Edit the canonical
> file here, then run `python scripts/sync_foundry_assets.py` and
> `python scripts/validate_assets.py`.


Licence

This work includes material from the AI Dungeon Master System (“AI-DM”) by Howard Branch, available at:
https://github.com/howard-branch/ai-dm

The AI-DM System is licensed under the Creative Commons Attribution 4.0 International License:
https://creativecommons.org/licenses/by/4.0/legalcode

### Third-Party Content Notice

This licence applies only to original content within this repository.

Any third-party intellectual property (including but not limited to Dungeons & Dragons and Magic: The Gathering) remains the property of its respective owners and is not covered by this licence.
