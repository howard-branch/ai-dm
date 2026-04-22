/**
 * Phase 3: structured combat macros.
 *
 * On `ready` we (idempotently) create five world macros. Each macro
 * gathers the controlling actor + first targeted token and POSTs a
 * structured `player_intent` envelope to Python; the
 * `StructuredIntentDispatcher` routes it through `IntentRouter` /
 * `CombatMachine` and pushes a `narration` event back into chat.
 */

const MACROS = [
  {
    name: "AI DM: Attack",
    img: "icons/skills/melee/strike-sword-blood-red.webp",
    body: `
const { sendStructuredIntent } = await import("/modules/ai-dm-bridge/scripts/socket_bridge.js");
const actor = token?.actor || game.user.character;
if (!actor) return ui.notifications?.warn("Select your token first.");
const target = game.user.targets.first();
if (!target) return ui.notifications?.warn("Target an enemy (T).");
sendStructuredIntent({
  type: "attack",
  actor_id: actor.id,
  target_id: target.actor?.id ?? target.id,
  scene_id: canvas?.scene?.id ?? null,
  user_id: game.user.id,
});
`.trim(),
  },
  {
    name: "AI DM: Dash",
    img: "icons/skills/movement/feet-winged-boots-brown.webp",
    body: `
const { sendStructuredIntent } = await import("/modules/ai-dm-bridge/scripts/socket_bridge.js");
const actor = token?.actor || game.user.character;
if (!actor) return ui.notifications?.warn("Select your token first.");
sendStructuredIntent({
  type: "dash",
  actor_id: actor.id,
  scene_id: canvas?.scene?.id ?? null,
  user_id: game.user.id,
});
`.trim(),
  },
  {
    name: "AI DM: Dodge",
    img: "icons/skills/melee/shield-block-gray.webp",
    body: `
const { sendStructuredIntent } = await import("/modules/ai-dm-bridge/scripts/socket_bridge.js");
const actor = token?.actor || game.user.character;
if (!actor) return ui.notifications?.warn("Select your token first.");
sendStructuredIntent({
  type: "dodge",
  actor_id: actor.id,
  scene_id: canvas?.scene?.id ?? null,
  user_id: game.user.id,
});
`.trim(),
  },
  {
    name: "AI DM: End Turn",
    img: "icons/skills/movement/arrow-down-blue.webp",
    body: `
const { sendStructuredIntent } = await import("/modules/ai-dm-bridge/scripts/socket_bridge.js");
const actor = token?.actor || game.user.character;
sendStructuredIntent({
  type: "end_turn",
  actor_id: actor?.id,
  scene_id: canvas?.scene?.id ?? null,
  user_id: game.user.id,
});
`.trim(),
  },
  {
    name: "AI DM: Use Skill",
    img: "icons/skills/trades/academics-study-reading-book.webp",
    body: `
const { sendStructuredIntent } = await import("/modules/ai-dm-bridge/scripts/socket_bridge.js");
const actor = token?.actor || game.user.character;
if (!actor) return ui.notifications?.warn("Select your token first.");
const SKILLS = ["acrobatics","animal_handling","arcana","athletics","deception","history","insight","intimidation","investigation","medicine","nature","perception","performance","persuasion","religion","sleight_of_hand","stealth","survival"];
const opts = SKILLS.map((s) => \`<option value="\${s}">\${s}</option>\`).join("");
new Dialog({
  title: "Use Skill",
  content: \`<form>
    <div class="form-group"><label>Skill</label><select name="skill">\${opts}</select></div>
    <div class="form-group"><label>DC (optional)</label><input name="dc" type="number" min="1" /></div>
  </form>\`,
  buttons: {
    go: {
      label: "Roll",
      callback: (html) => {
        const skill = String(html.find('select[name="skill"]').val() || "");
        const dcRaw = html.find('input[name="dc"]').val();
        const dc = dcRaw ? Number(dcRaw) : null;
        sendStructuredIntent({
          type: "skill_check",
          actor_id: actor.id,
          skill,
          dc,
          scene_id: canvas?.scene?.id ?? null,
          user_id: game.user.id,
        });
      },
    },
    cancel: { label: "Cancel" },
  },
  default: "go",
}).render(true);
`.trim(),
  },
];

async function ensureMacros() {
  if (!game.user.isGM) return; // only the GM seeds macros
  for (const spec of MACROS) {
    const existing = game.macros.find((m) => m.name === spec.name);
    if (existing) {
      // Refresh the body so updates ship without manual deletion.
      try {
        await existing.update({ command: spec.body, img: spec.img });
      } catch (err) {
        console.warn("AI DM Bridge: failed to update macro", spec.name, err);
      }
      continue;
    }
    try {
      await Macro.create({
        name: spec.name,
        type: "script",
        img: spec.img,
        scope: "global",
        command: spec.body,
      });
      console.log("AI DM Bridge: created macro", spec.name);
    } catch (err) {
      console.warn("AI DM Bridge: failed to create macro", spec.name, err);
    }
  }
}

Hooks.once("ready", ensureMacros);

