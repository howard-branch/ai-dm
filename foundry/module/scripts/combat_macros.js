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

// Enumerate equipped weapons (dnd5e). Fall back to ALL weapons in
// inventory when nothing is flagged equipped (older sheets / NPCs).
const items = actor.items?.contents ?? [];
const weapons = items.filter((i) => i.type === "weapon");
const equipped = weapons.filter((w) => w.system?.equipped);
const choices = (equipped.length ? equipped : weapons);

function _slug(w) {
  // Prefer the 5e identifier (matches our SRD weapons.json keys), then
  // fall back to a normalised name slug.
  return (w.system?.identifier
          || (w.name || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, ""))
         || "weapon";
}

function _send(weaponSlug, twoHanded) {
  sendStructuredIntent({
    type: "attack",
    actor_id: actor.id,
    target_id: target.actor?.id ?? target.id,
    weapon: weaponSlug,
    two_handed: !!twoHanded,
    scene_id: canvas?.scene?.id ?? null,
    user_id: game.user.id,
  });
}

if (choices.length === 0) {
  ui.notifications?.warn("No weapons in inventory — using unarmed strike.");
  _send(null, false);
  return;
}

if (choices.length === 1) {
  const w = choices[0];
  const versatile = (w.system?.properties?.ver) || (w.system?.damage?.versatile);
  // Auto-pick when only one option; surface a versatile two-handed
  // toggle if the weapon supports it.
  if (versatile) {
    new Dialog({
      title: \`Attack with \${w.name}\`,
      content: \`<p>\${w.name} (versatile). One-handed or two-handed?</p>\`,
      buttons: {
        one:  { label: "One-handed", callback: () => _send(_slug(w), false) },
        two:  { label: "Two-handed", callback: () => _send(_slug(w), true) },
      },
      default: "one",
    }).render(true);
  } else {
    _send(_slug(w), false);
  }
  return;
}

const opts = choices.map((w, i) =>
  \`<option value="\${i}">\${w.name}\${w.system?.equipped ? "" : " (carried)"}</option>\`
).join("");
new Dialog({
  title: "Attack — choose a weapon",
  content: \`<form>
    <div class="form-group"><label>Weapon</label>
      <select name="w">\${opts}</select>
    </div>
    <div class="form-group">
      <label><input type="checkbox" name="th" /> Two-handed (versatile)</label>
    </div>
  </form>\`,
  buttons: {
    go: {
      label: "Strike",
      callback: (html) => {
        const idx = Number(html.find('select[name="w"]').val() || 0);
        const th = !!html.find('input[name="th"]').is(":checked");
        const w = choices[idx] || choices[0];
        _send(_slug(w), th);
      },
    },
    cancel: { label: "Cancel" },
  },
  default: "go",
}).render(true);
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

