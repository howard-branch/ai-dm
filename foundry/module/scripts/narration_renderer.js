/**
 * Render an inbound {event:"narration"} payload as Foundry chat.
 *
 * Payload shape (from PlayerInputDispatcher / opening_scene):
 *   {
 *     actor_id, user_id,
 *     narration, dialogue: [{npc_id, text, tone}],
 *     commands_ok, whisper_to,
 *     // optional, set by the opening narration:
 *     interactables: [{name, kind, hint?}],
 *     exits:         [{name, to?}],
 *     metadata:      {kind: "opening", scene_id, scene_name}
 *   }
 */
function escapeHTML(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const KIND_ICON = {
  npc:      "🗣",
  object:   "✦",
  exit:     "↦",
  entrance: "↤",
  hazard:   "⚠",
  anchor:   "•",
};

function renderInteractablesBlock(items) {
  if (!Array.isArray(items) || items.length === 0) return "";
  const lis = items.map((it) => {
    const icon = KIND_ICON[it?.kind] || "•";
    const name = `<strong>${escapeHTML(it?.name)}</strong>`;
    const hint = it?.hint ? ` <em>(${escapeHTML(it.hint)})</em>` : "";
    return `<li>${icon} ${name}${hint}</li>`;
  }).join("");
  return (
    `<div class="ai-dm-interactables">` +
    `<div class="ai-dm-section-label">You can interact with:</div>` +
    `<ul>${lis}</ul></div>`
  );
}

function renderExitsBlock(exits) {
  if (!Array.isArray(exits) || exits.length === 0) return "";
  const parts = exits.map((e) => `<strong>${escapeHTML(e?.name)}</strong>`);
  return (
    `<div class="ai-dm-exits">` +
    `<div class="ai-dm-section-label">Exits:</div> ${parts.join(" · ")}` +
    `</div>`
  );
}

export async function renderNarration(payload, { createChatMessage = true } = {}) {
  const narration = (payload?.narration || "").trim();
  const dialogue = Array.isArray(payload?.dialogue) ? payload.dialogue : [];
  const meta = payload?.metadata || {};
  const isOpening = meta.kind === "opening";

  const messages = [];

  if (narration || isOpening) {
    let header = "";
    if (isOpening) {
      const sceneName = escapeHTML(meta.scene_name || meta.scene_id || "Scene");
      header =
        `<div class="ai-dm-scene-header" ` +
        `style="border-bottom:1px solid #888;margin-bottom:0.4em;` +
        `padding-bottom:0.2em;font-weight:bold">` +
        `📍 ${sceneName} — the adventure begins</div>`;
    }
    const prose = narration
      ? `<div class="ai-dm-narration">${escapeHTML(narration).replace(/\n/g, "<br>")}</div>`
      : "";
    // For opening narrations the prose paragraph already weaves the
    // interactables and exits into the narrative, so we deliberately
    // do NOT render the bullet-list blocks below — they would just
    // duplicate text the player already read.
    const interactables = isOpening ? "" : renderInteractablesBlock(payload?.interactables);
    const exits = isOpening ? "" : renderExitsBlock(payload?.exits);
    const html = header + prose + interactables + exits;

    const msg = {
      speaker: { alias: isOpening ? "Scene" : "DM" },
      content: html,
    };
    if (payload?.whisper_to) msg.whisper = [payload.whisper_to];
    messages.push(msg);
  }

  for (const line of dialogue) {
    const tone = line.tone ? ` <em>(${escapeHTML(line.tone)})</em>` : "";
    messages.push({
      speaker: { alias: line.npc_id || "NPC" },
      content: `<div class="ai-dm-dialogue">${escapeHTML(line.text)}${tone}</div>`,
    });
  }

  if (payload && payload.commands_ok === false) {
    messages.push({
      speaker: { alias: "AI DM" },
      content: `<div class="ai-dm-error">⚠ Some Foundry commands failed.</div>`,
      whisper: [game.user.id],
    });
  }

  // Always append to the local persistent narration-log window so every
  // client keeps a full transcript regardless of the chat-sidebar state.
  try {
    const { NarrationLog } = await import("./narration_log.js");
    for (const m of messages) {
      // Skip whispers that aren't for us — they shouldn't show up in
      // someone else's persistent log either.
      if (m.whisper && Array.isArray(m.whisper)
          && !m.whisper.includes(game.user?.id)) continue;
      NarrationLog.append({
        kind: isOpening ? "opening" : "narration",
        speaker: m?.speaker?.alias || "DM",
        html: m?.content || "",
      });
    }
  } catch (err) { /* log unavailable; ignore */ }

  // Only the elected renderer actually creates the ChatMessage — see
  // ``socket_bridge.js::isElectedRenderer``. Foundry's own server
  // replicates that ChatMessage to every other tab so the chat
  // sidebar stays in sync with no duplicates.
  if (!createChatMessage) return;
  for (const m of messages) {
    try {
      await ChatMessage.create(m);
    } catch (err) {
      console.warn("AI DM Bridge: ChatMessage.create failed", err, m);
    }
  }
}



