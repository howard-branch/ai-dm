/* AI DM — interactive roll prompt + DM roll echo cards.
 *
 * Two inbound events are registered from socket_bridge.js:
 *
 *   request_player_roll  — Python is asking the player to roll. We
 *                          create a chat card with a "Roll" button.
 *                          On click, we evaluate a Foundry Roll
 *                          (which animates via Dice So Nice if
 *                          installed) and POST the outcome back to
 *                          Python via ``player_roll_resolved``.
 *
 *   chat_roll            — Python already rolled (DM/auto-roll). We
 *                          just render a styled chat message; result
 *                          numbers come from Python (authoritative).
 *
 * The roll button is delegated through Hooks.on("renderChatMessage")
 * so it survives reload (chat messages persist; flags do too).
 */

import { sendEventToPython } from "./socket_bridge.js";
import { PendingRollsTray } from "./pending_rolls_tray.js";

const FLAG_SCOPE = "ai-dm-bridge";
const FLAG_KEY = "rollRequest";
const DM_FLAG_KEY = "dmRoll";

// Build the default formula for a request when Python only said
// "skill: perception". The real modifier comes from the actor sheet
// at click time, so we just do 1d20[+/-mod] here as a fallback.
function defaultFormula(payload) {
  if (payload?.formula) return payload.formula;
  const adv = String(payload?.advantage || "normal").toLowerCase();
  if (adv === "advantage") return "2d20kh1";
  if (adv === "disadvantage") return "2d20kl1";
  return "1d20";
}

// Look up an actor's skill / save / ability modifier from its sheet.
// Returns 0 if unavailable so the player can still roll.
function lookupModifier(actor, payload) {
  if (!actor?.system) return 0;
  const t = String(payload?.roll_type || "").toLowerCase();
  const k = String(payload?.key || "").toLowerCase();
  try {
    if (t === "skill" && k) {
      const sk = actor.system.skills?.[k] ?? actor.system.skills?.[k.slice(0, 3)];
      if (sk?.total !== undefined) return Number(sk.total) || 0;
      if (sk?.mod !== undefined) return Number(sk.mod) || 0;
    }
    if (t === "save" && k) {
      const ab = actor.system.abilities?.[k.slice(0, 3)];
      if (ab?.save !== undefined) return Number(ab.save) || 0;
    }
    if (t === "ability" && k) {
      const ab = actor.system.abilities?.[k.slice(0, 3)];
      if (ab?.mod !== undefined) return Number(ab.mod) || 0;
    }
  } catch (e) { /* ignore — fall back to 0 */ }
  return 0;
}

function resolveActor(actorId) {
  if (!actorId) return null;
  return (
    game.actors?.get(actorId) ??
    game.actors?.find((a) => (a.name || "").toLowerCase() === String(actorId).toLowerCase()) ??
    null
  );
}

function shouldRenderForCurrentUser(payload) {
  // Whisper-or-public visibility: a "self" prompt only renders for
  // the user that owns the actor; "gm" only for the GM; "public"
  // shows for everyone (the elected client below decides who creates
  // the ChatMessage so we don't get N copies on the wire).
  const vis = String(payload?.visibility || "public").toLowerCase();
  if (vis === "public") return true;
  if (vis === "gm") return !!game?.user?.isGM;
  if (vis === "self") {
    const actor = resolveActor(payload?.actor_id);
    if (!actor) return !!game?.user?.isGM;
    return actor.testUserPermission(game.user, "OWNER");
  }
  return true;
}

// Election helper — same algorithm socket_bridge uses for the
// narration card. We can't import its private function so we
// duplicate the rule (lowest-id active GM, else lowest-id user).
function _isElectedRenderer() {
  const me = game?.user;
  if (!me) return false;
  const active = (game.users?.contents ?? game.users ?? [])
      .filter((u) => u?.active);
  if (!active.length) return true;
  const gms = active.filter((u) => u.isGM);
  if (gms.length) {
    gms.sort((a, b) => String(a.id).localeCompare(String(b.id)));
    return me.id === gms[0].id;
  }
  active.sort((a, b) => String(a.id).localeCompare(String(b.id)));
  return me.id === active[0].id;
}

/**
 * Render an interactive roll prompt as a Foundry chat card.
 * Called from socket_bridge.js when a ``request_player_roll`` event
 * arrives from Python.
 */
export async function renderRollPrompt(payload) {
  if (!payload?.request_id) {
    console.warn("AI DM Bridge: request_player_roll missing request_id", payload);
    return;
  }
  if (!shouldRenderForCurrentUser(payload)) return;

  // Add a sticky entry to the per-client Pending Rolls tray. This is
  // independent of the chat sidebar so the prompt stays visible even
  // if the chat scrolls past or the toast notification fades.
  try { PendingRollsTray.add(payload); } catch (err) { /* non-fatal */ }

  // Only the elected client creates the persisted ChatMessage —
  // Foundry replicates it to every other tab automatically. Without
  // this gate we'd get N copies of the prompt card.
  if (!_isElectedRenderer()) return;

  const actor = resolveActor(payload.actor_id);
  const actorName = actor?.name || payload.actor_id || "the party";
  const prompt = payload.prompt_text || "Make a roll";
  const dcText = (payload.dc != null) ? ` <span class="aidm-roll-dc">DC ${payload.dc}</span>` : "";
  const adv = String(payload.advantage || "normal").toLowerCase();
  const advBadge = (adv === "advantage")
      ? ` <span class="aidm-roll-adv">[adv]</span>`
      : (adv === "disadvantage")
          ? ` <span class="aidm-roll-dis">[dis]</span>`
          : "";

  const content = `
    <div class="aidm-roll-card" data-request-id="${payload.request_id}">
      <div class="aidm-roll-actor"><strong>${actorName}</strong></div>
      <div class="aidm-roll-prompt">${prompt}${dcText}${advBadge}</div>
      <button type="button" class="aidm-roll-btn" data-request-id="${payload.request_id}">
        🎲 Roll
      </button>
    </div>
  `;

  const flagData = {
    requestId: payload.request_id,
    payload: payload,
    state: "pending",
  };

  const messageData = {
    user: game.user.id,
    speaker: { alias: actorName },
    content,
    flags: { [FLAG_SCOPE]: { [FLAG_KEY]: flagData } },
  };

  // GM-whisper for visibility=="gm" so other players don't see
  // hidden checks.
  const vis = String(payload.visibility || "public").toLowerCase();
  if (vis === "gm") {
    messageData.whisper = ChatMessage.getWhisperRecipients("GM").map((u) => u.id);
  }

  try {
    await ChatMessage.create(messageData);
    console.log("AI DM Bridge: roll prompt created", payload.request_id);
  } catch (err) {
    console.warn("AI DM Bridge: roll prompt create failed", err);
  }
}

/**
 * Render a DM-side (already-resolved) roll as a styled chat card.
 * Called when a ``chat_roll`` event arrives from Python.
 */
export async function renderChatRoll(payload) {
  if (!payload?.request_id) return;
  // Auto-roll (or any DM-side roll) settled this request — clear the
  // matching tray entry on EVERY client. Do this before the elected
  // gate so non-elected clients also drop the sticky button.
  try { PendingRollsTray.remove(payload.request_id); } catch (err) { /* ignore */ }
  if (!_isElectedRenderer()) return;
  const vis = String(payload.visibility || "gm").toLowerCase();
  const actor = resolveActor(payload.actor_id);
  const actorName = actor?.name || payload.actor_id || "DM";
  const tag = `${payload.roll_type || "roll"}${payload.key ? "/" + payload.key : ""}`;
  const dice = (payload.kept && payload.kept.length)
      ? payload.kept.join(", ")
      : (payload.rolls || []).join(", ");
  const target = (payload.dc != null) ? `vs DC ${payload.dc}`
      : (payload.ac != null) ? `vs AC ${payload.ac}` : "";
  const outcome = (payload.success === true) ? "✅ success"
      : (payload.success === false) ? "❌ failure" : "";
  const reason = payload.reason ? `<div class="aidm-roll-reason">${payload.reason}</div>` : "";
  const content = `
    <div class="aidm-roll-card aidm-dmroll-card">
      <div class="aidm-roll-actor"><strong>${actorName}</strong> <span class="aidm-roll-tag">[${tag}]</span></div>
      <div class="aidm-roll-result">
        <code>${payload.formula || "?"}</code> →
        <strong>${payload.total}</strong>
        ${dice ? `<span class="aidm-roll-dice">(d20: ${dice}${payload.modifier ? `, mod ${payload.modifier >= 0 ? "+" : ""}${payload.modifier}` : ""})</span>` : ""}
      </div>
      <div class="aidm-roll-outcome">${target} ${outcome}</div>
      ${reason}
    </div>
  `;

  const messageData = {
    user: game.user.id,
    speaker: { alias: actorName },
    content,
    flags: { [FLAG_SCOPE]: { [DM_FLAG_KEY]: payload } },
  };
  if (vis === "gm") {
    messageData.whisper = ChatMessage.getWhisperRecipients("GM").map((u) => u.id);
  }
  try {
    await ChatMessage.create(messageData);
  } catch (err) {
    console.warn("AI DM Bridge: chat_roll render failed", err);
  }
}

// ---------------------------------------------------------------- //
// Shared roll execution. Used by:
//   • the chat-card "Roll" button (`_onRollButtonClick` below), and
//   • the Pending Rolls tray (`pending_rolls_tray.js`).
//
// Evaluates a real Foundry Roll (Dice So Nice picks it up
// automatically via `roll.toMessage`), POSTs the outcome back to
// Python via ``player_roll_resolved``, and clears the tray entry.
// Returns the resolved-roll envelope (or null on failure).
// ---------------------------------------------------------------- //

export async function performRoll(payload) {
  if (!payload?.request_id) return null;
  const actor = resolveActor(payload.actor_id);
  const modifier = lookupModifier(actor, payload);
  let formula = defaultFormula(payload);
  if (modifier && /^\s*\d*d20(?:k[hl]\d+)?\s*$/i.test(formula)) {
    formula = modifier >= 0 ? `${formula}+${modifier}` : `${formula}${modifier}`;
  }

  const t0 = performance.now();
  let roll;
  try {
    roll = new Roll(formula);
    await roll.evaluate({ async: true });
  } catch (err) {
    console.warn("AI DM Bridge: roll evaluation failed", err);
    return null;
  }

  const speaker = { alias: actor?.name || payload.actor_id || "Player" };
  try {
    await roll.toMessage({
      speaker,
      flavor: payload.prompt_text || "Roll",
    });
  } catch (err) {
    console.warn("AI DM Bridge: roll.toMessage failed", err);
  }

  const total = Number(roll.total) || 0;
  let success = null;
  if (payload.dc != null) success = total >= Number(payload.dc);
  if (payload.ac != null) success = total >= Number(payload.ac);
  const d20Term = (roll.dice || []).find((d) => d.faces === 20);
  const kept = d20Term ? d20Term.results.filter((r) => r.active).map((r) => r.result) : [];
  const allDice = d20Term ? d20Term.results.map((r) => r.result) : [];
  const nat = kept[0] ?? null;

  const resolved = {
    request_id: payload.request_id,
    actor_id: payload.actor_id,
    roll_type: payload.roll_type,
    key: payload.key,
    formula,
    total,
    modifier,
    rolls: allDice,
    kept,
    advantage: payload.advantage || "normal",
    crit: nat === 20,
    fumble: nat === 1,
    dc: payload.dc ?? null,
    ac: payload.ac ?? null,
    success,
    elapsed_ms: Math.round(performance.now() - t0),
  };

  try {
    sendEventToPython("player_roll_resolved", resolved);
    console.log("AI DM Bridge: player_roll_resolved sent",
                payload.request_id, "total=", total);
  } catch (err) {
    console.warn("AI DM Bridge: send player_roll_resolved failed", err);
  }

  // Clear the sticky tray card immediately on this client; the
  // ``updateChatMessage`` hook in pending_rolls_tray.js takes care
  // of the other clients once the chat-card flag is bumped to
  // "resolved" below (or below in `_onRollButtonClick`).
  try { PendingRollsTray.remove(payload.request_id); } catch (err) { /* ignore */ }

  return { resolved, total, success };
}

// ---------------------------------------------------------------- //
// Click handler for the chat-card button. Bound once on ready via
// `installRollPromptHooks`. Walks up to the message, runs the
// shared `performRoll`, then marks the chat card as consumed so
// it can't be rolled twice.
// ---------------------------------------------------------------- //

async function _onRollButtonClick(event, message) {
  event.preventDefault();
  const flag = message.getFlag(FLAG_SCOPE, FLAG_KEY);
  if (!flag || flag.state !== "pending") return;
  const payload = flag.payload || {};
  const out = await performRoll(payload);
  if (!out) return;
  const { total, success } = out;

  try {
    await message.update({
      [`flags.${FLAG_SCOPE}.${FLAG_KEY}.state`]: "resolved",
      content: message.content.replace(
        /<button[\s\S]*?<\/button>/,
        `<div class="aidm-roll-done">rolled <strong>${total}</strong>${
            success === true ? " ✅" : (success === false ? " ❌" : "")
          }</div>`
      ),
    });
  } catch (err) {
    /* ignore — non-fatal display update */
  }
}

/**
 * Install the global click handler. Idempotent.
 */
export function installRollPromptHooks() {
  Hooks.on("renderChatMessage", (message, html) => {
    const flag = message.getFlag?.(FLAG_SCOPE, FLAG_KEY);
    if (!flag) return;
    html.find(`button.aidm-roll-btn[data-request-id="${flag.requestId}"]`)
        .off("click.aidm")
        .on("click.aidm", (ev) => _onRollButtonClick(ev, message));
  });
}

