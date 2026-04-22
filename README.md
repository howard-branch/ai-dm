# AI Dungeon Master

Hybrid AI + Foundry VTT Dungeon Master framework.

## Architecture

- **Foundry**: rules, sheets, dice, combat, tokens, scenes
- **Python app**: orchestration, memory, persistence, UI, audio
- **AI**: structured narrative output only

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m ai_dm.main
```

## Project layout

- `assets/` shared rules/reference assets
- `data/` mutable runtime state and saves
- `~/dnd/campaigns/` authored campaign packs (outside the repo by default)
- `foundry/` Foundry module bridge
- `src/ai_dm/` Python application code
- `tests/` unit and integration tests

## Phase 3 features

- **Rules engine** (`ai_dm.rules`) — seedable dice, ability checks, attacks,
  damage, conditions, and a typed `HouseRule` model
  (`assets/rules/house_rules.json`). MVP scope: checks · attacks · damage ·
  conditions. Spells deferred.
- **Player intent parser** (`ai_dm.ai.intent_parser`) — regex/keyword
  fast-path with LLM fallback (`AIClient`); strict `PlayerIntent` schema.
- **Event triggers** (`ai_dm.orchestration.triggers`) — declarative rules
  loaded from the active campaign pack's `triggers/*.{json,yaml}`, with
  predicate DSL, bounded reentrancy, `once=true` persistence.
- **TTS narration** (`ai_dm.audio`) — `edge-tts` by default with
  automatic `piper` fallback; voice-per-NPC profile in the active
  campaign pack's `voices.json`; threaded priority queue; narrator
  output is published as `narrator.output_ready` and routed through
  `NarrationDispatcher`.
- **Long-form story planner** (`ai_dm.ai.planner`) — chapters/scenes/beats
  loaded from the active campaign pack's `chapters/`, append-only
  `Timeline`, `summary_for_prompt()` injected into the narrator context.
- **Foundry sync** (`ai_dm.foundry`) — `JournalService` (create/append),
  `Reconciler` (diff + auto-heal HP/name drift), and a `SocketBridge` that
  re-publishes inbound Foundry events as `foundry.token_moved`,
  `foundry.actor_updated`, `foundry.scene_activated` on the local
  `EventBus`. JS side: `foundry/module/scripts/journal_commands.js` plus
  inbound hooks in `socket_bridge.js`.

All Phase-3 state (arc, timeline, fired triggers, journals, actor cache)
round-trips through `CampaignStore` (`schema_version: 3`, with auto-migration
from v1 / v2).

## Authority model

**Python is authoritative.** Foundry is a rendering / execution layer.

The `InboundArbiter` (`ai_dm.foundry.authority`) sits between the
`SocketBridge` and the rest of the app. Every inbound `foundry.*` event
is classified:

| Classification | Trigger | Effect |
| --- | --- | --- |
| `self_echo`   | The change matches an outbound write we just sent (deduplicated by `EchoSuppressor`, TTL ≈ 3 s) | dropped silently |
| `absorb`      | External (GM) change, policy = `absorb` (default for token / actor / scene) | merged into Python state caches |
| `revert`      | External change, policy = `revert` | inverse command pushed back through the `CommandRouter` |
| `ignore`      | Unknown event type or policy = `ignore` | logged only |

Each classification is re-published as a structured `authority.*` event,
e.g. `authority.token_moved`, so other layers (logging, narrator
follow-up, save state) can react.

Echo suppression is wired automatically: `CommandRouter.pre_dispatch_hooks`
fingerprints every outbound write into the suppressor before it leaves
the queue, so corrective writes from the arbiter itself never loop.

Per-event policy is configurable in `config/settings.yaml` under
`authority.policy`.

## Pluggable campaigns

Campaign-specific content lives in self-contained **campaign packs**
under `~/dnd/campaigns/<slug>/` by default:

```
~/dnd/campaigns/morgana/
  campaign.yaml        # id, name, version, optional path overrides
  chapters/
  locations/
  prompts/             # system_narrator.txt, output_schema.json, ...
  triggers/
  world/
  voices.json
  characters/seed/     # starter PCs/companions (copied to state on first run)
  portraits/  maps/  audio/
```

Per-campaign **mutable** state (live characters, saves, backups,
memory) is written outside the pack so packs stay read-only and
distributable:

```
data/campaigns/<slug>/
  characters/   # live, mutated character sheets
  saves/        # campaign_state.json + backups/
  memory/  cache/
```

Switching campaigns is one line in `config/settings.yaml`:

```yaml
campaigns:
  root: ~/dnd/campaigns
  active: morgana          # slug under root, OR a path to a pack
  state_root: data/campaigns
```

Shared content (D&D 5e reference, house rules under `assets/rules/`)
stays put — it is **not** part of any campaign pack.


