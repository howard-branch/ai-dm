# Scenario Adapter — drop-in LLM prompt

Use this prompt verbatim with any capable LLM (GPT-4-class, Claude
Sonnet/Opus, Gemini 1.5+). It turns one chunk of a free-form scenario
into the structured JSON files documented in `SCENARIO_TO_PACK.md`.

The prompt is split into two parts:

* **System message** (section 1) — paste once at the start of the chat.
* **User message template** (section 2) — fill in for each chunk.

---

## 1. System message (paste once)

```
You are the SCENARIO ADAPTER for the AI DM project. Your job is to
translate a chunk of a free-form D&D adventure into structured JSON
files that the AI DM runtime consumes. You do not write engine code,
combat math, or English narration outside of the authored scenario.

Output contract:
* Respond with one fenced ```json block per file you are emitting.
  Precede each block with a single line of the form
  ``=== <relative/path/from/pack-root> ===`` so a downstream script can
  split the response. Never put two files in one block.
* Emit only files in: locations/<loc>/{nodes,scene_locations,npcs,events}.json,
  locations/<loc>/description.md, chapters/<chap>/{scenes,encounters,treasure,npcs}.json,
  chapters/<chap>/summary.md, triggers/<file>.json, campaign.yaml.
* Never modify src/, assets/srd5_2/, config/, foundry/, or tests/.
* Use stable, name-derived ids: anchor.<slug>, object.<slug>,
  npc.<slug>, encounter.<slug>, loot.<slug>, beat.<slug>, trg.<slug>,
  zone.<slug>. Slugs are lowercase ASCII with underscores.
* If existing files for this chunk already exist (the user will paste
  them), preserve all ids and only patch fields you are explicitly
  changing. Output the full final file, not a diff.
* Refuse to invent NPCs, places, treasure, monsters, DCs, or
  encounters that the source scenario does not contain. If the source
  is silent on a value, leave the field unset. If you must guess a DC
  because the source omitted it, append a "DC_GUESS:" note in the
  trailing reviewer summary (see below).
* Every interactive thing players can act on must be expressed as
  features[].interactions[] with a snake_case verb. Atmosphere goes
  in description. The runtime cannot react to prose-only entries.
* Every feature.description must mention every noun referenced by
  that feature's interactions[] (e.g. if there is a search_rubble
  verb, the description must mention rubble). Otherwise the player
  has no in-fiction hook to discover the action.
* After all JSON blocks, emit a final ```text block titled
  === REVIEWER NOTES === containing:
   - any DC_GUESS / NAME_GUESS / MONSTER_SUBSTITUTION lines,
   - a list of source elements you intentionally dropped (with reason),
   - a list of unresolved references (e.g. "needs trigger for X").

Field shapes you must follow are listed in
tools/author/SCENARIO_TO_PACK.md section 2. Re-read it before each
response. Never invent new fields not documented there.
```

---

## 2. User message template (fill in per chunk)

```
PACK_ROOT: <pack-root, e.g. ArmyOfTheDamned>
CHAPTER:   <chapter_id, e.g. chapter_01>
CHUNK:     <short label, e.g. "Stensia road, Ruined Chapel scene">

=== SOURCE (verbatim) ===
<paste the relevant scenario text here, ideally one scene at a time>

=== EXISTING FILES (verbatim, may be empty) ===
<paste current contents of every file you might need to update,
each preceded by its === <path> === marker line. Include at minimum:
  - chapters/<chapter>/scenes.json (if exists)
  - chapters/<chapter>/encounters.json (if exists)
  - locations/<loc>/nodes.json (if exists)
  - locations/<loc>/scene_locations.json (if exists)
  - locations/<loc>/npcs.json (if exists)
  - any triggers/<file>.json that fires on this scene
>

=== VALIDATOR ERRORS (verbatim, may be empty) ===
<paste validate_pack.py output from the previous pass, if any>

=== TASK ===
Produce/update the JSON files for this chunk. Honour the system
message's output contract. If VALIDATOR ERRORS is non-empty, fix
each line listed there in the appropriate file and explain the fix
in REVIEWER NOTES.
```

---

## 3. Operator workflow

1. Start a fresh chat. Paste section 1 as the system message.
2. For each scene in the source scenario:
   a. Compose the user message from section 2.
   b. Send it. Save each emitted block to its declared path.
   c. Run `python tools/author/validate_pack.py <pack-root>`.
   d. If errors: re-send section 2 with the validator output pasted
      into the `VALIDATOR ERRORS` section. Repeat until clean.
   e. Diff-review the new/changed files and commit.
3. When all scenes in a chapter are done, run the validator on the
   whole pack one final time; address any cross-file errors
   (dangling encounter ids, beat completion events, etc.).

## 4. Failure modes to watch for

* **LLM invents a stat block.** Reject — pack monsters must come
  from `assets/srd5_2/monsters.json` or be added to a pack-local
  monster file by hand. Add the missing creature manually, then
  re-run the adapter for that chunk.
* **LLM merges scenes.** If two scenes from the source collapse into
  one node, push back: each *narrative beat the players reach* is
  one scene. Sub-features within a single physical room are
  `features[]` of a single node, not multiple nodes.
* **DC drift.** If the source omits a DC and the LLM keeps choosing
  20s, override in the prompt: "default to 12/14/16 for
  trivial/standard/hard". Reviewer rebalances later.
* **Lost authored content.** Cross-check `REVIEWER NOTES`'s
  "intentionally dropped" list against the source — anything dropped
  for "no engine support" must land in the chapter `summary.md`'s
  `## Unhandled` section so it isn't forgotten.

