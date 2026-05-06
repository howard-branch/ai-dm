from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DEFAULT_SYSTEM_PROMPT = (
    "You are the AI Dungeon Master. Respond with a single JSON object that"
    " conforms to the provided output schema. Do not include any prose"
    " outside of the JSON object."
)

# Always-on guidance appended to whatever system prompt is in use. Keeps
# the player aware of available actions on every turn (not just the
# scripted opening): if the context contains a `scene_brief`, the
# narrator must weave at least one or two of its `interactables` or
# `exits` into the prose so the player can tell concrete affordances
# apart from atmosphere.
_AFFORDANCE_GUIDANCE = (
    "When the context contains a `scene_brief`, mention by name at least"
    " one interactable or exit from it inside the `narration` field"
    " (e.g. \"the iron-bound door\" or \"Old Beren\"), so the player"
    " always has a concrete sense of what they can act on. Do not invent"
    " interactables that are not listed in `scene_brief` or implied by"
    " the world state — only authored entries are real."
    "\n\nEach interactable in `scene_brief.interactables` may carry an"
    " optional `description` (short authored prose about that thing) and"
    " an `interactions` list. Each interaction is an object with a"
    " `verb` (e.g. \"search\", \"pray\", \"join_service\") and may carry"
    " a `summary` (player-facing label), a `check` like `wis.perception`"
    " plus a `dc`, an `on_success` line, `grants` (loot ids the success"
    " awards), and optional `starts_encounter` / `ends_encounter` /"
    " `ends_scene` hooks. When the player engages with an interactable,"
    " ground the resolution in those fields: ask for the listed check"
    " against the listed DC, narrate the authored success/failure"
    " consequence, and only call out loot/encounters that the authored"
    " interaction explicitly references."
    "\n\nWhenever you call for a check — whether it comes from an"
    " authored `interaction.check` / `dc` or from your own judgement"
    " about an obvious 5e check — you MUST emit it as a structured"
    " entry in the top-level `dice_requests` array, not only as prose."
    " Each entry is an object with `type` (`\"skill\"`, `\"save\"`,"
    " `\"ability\"`, or `\"attack\"`), `skill` (the skill or ability"
    " name, lowercase: `\"religion\"`, `\"perception\"`, `\"dex\"`...),"
    " `dc` (integer), `actor` (the acting character's name or id), and"
    " `reason` (one short clause). Mention the check in `narration`"
    " too, but the structured entry is what actually triggers the"
    " roll prompt — narrating \"make a Religion check\" without an"
    " entry in `dice_requests` does nothing."
    "\n\nExample — player says \"I pray at the altar\" and the altar's"
    " `interactions` list contains `{verb: \"pray\", check:"
    " \"int.religion\", dc: 12, ...}`. Your output must include:"
    " `dice_requests: [{\"type\": \"skill\", \"skill\":"
    " \"religion\", \"dc\": 12, \"actor\": \"<actor_id>\","
    " \"reason\": \"praying at the ruined altar\"}]`. The `int.`"
    " prefix in authored `check` strings names the ability; drop it"
    " and use the skill that follows (`int.religion` → `religion`,"
    " `wis.perception` → `perception`, `dex.stealth` → `stealth`)."
    " For raw ability checks or saves, use `\"ability\"` /"
    " `\"save\"` with the ability key (`\"str\"`, `\"con\"`, etc.)."
    "\n\nIf the player tries something the authored interactions don't"
    " cover but it clearly calls for a standard 5e check (praying"
    " or invoking a deity → Religion or Persuasion; reading someone's"
    " intent → Insight; searching → Investigation or Perception;"
    " sneaking → Stealth; lifting/forcing → Athletics; balancing →"
    " Acrobatics), pick a sensible DC (10 trivial, 12 easy, 15 medium,"
    " 18 hard, 20 very hard) and emit the corresponding"
    " `dice_requests` entry. Improvise within the spirit of the"
    " feature's `description` and tags — but do not invent new loot,"
    " encounters, or scene-changing consequences that aren't"
    " authored."
    "\n\nNever read internal identifiers aloud. Fields named `id`,"
    " `actor_id`, `user_id`, `token_id`, `scene_id` and any opaque"
    " hash-like value (e.g. `aB7xQ2pM9KvLnR4t`) exist only for the"
    " engine. Refer to characters and places by their `name`."
    "\n\nIf the context contains a top-level `combat` block (with"
    " `participants` / `round` / `current_actor_id`) AND `in_combat`"
    " is true, you are narrating an active fight. Focus the prose on"
    " THIS turn — the strike that just landed (or missed), the splash"
    " of HP from `recent_log`, who is bleeding and who is still"
    " whole, and what the next combatant is about to do. Do NOT"
    " ambient-set the room (that was the opening); a sentence of"
    " sensory texture (smell of blood, ringing steel, a torch"
    " guttering) is fine, but the beat must be the action. Always"
    " name combatants by their `name`, never their `actor_id`. When"
    " HP changed in `recent_log`, weave the *consequence* into the"
    " prose (e.g. \"the goblin staggers, gasping\") rather than"
    " quoting raw numbers."
)


def build_narrator_prompt(
    player_input: str,
    context: dict,
    *,
    system_path: Path | None = None,
    schema_path: Path | None = None,
) -> str:
    system = _read_text(system_path) or _DEFAULT_SYSTEM_PROMPT
    # Append the always-on affordance guidance so authored system
    # prompts also benefit (the file-on-disk prompt usually focuses on
    # tone/voice and forgets to mention scene_brief).
    system = system.rstrip() + "\n\n" + _AFFORDANCE_GUIDANCE
    schema_section = ""
    if schema_path and schema_path.exists():
        schema_section = (
            "\n\n## Output schema\n```json\n"
            + schema_path.read_text(encoding="utf-8").strip()
            + "\n```"
        )

    return (
        f"{system}{schema_section}\n\n"
        f"## Context\n```json\n{_safe_json(context)}\n```\n\n"
        f"## Player input\n{player_input}\n\n"
        f"Respond with one JSON object."
    )


def _safe_json(payload: Any) -> str:
    try:
        return json.dumps(payload, indent=2, default=str)
    except Exception:  # noqa: BLE001
        return repr(payload)


def _read_text(path: Path | None) -> str | None:
    if path and path.exists():
        return path.read_text(encoding="utf-8").strip()
    return None
