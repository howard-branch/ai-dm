/**
 * Render an inbound {event:"narration"} payload as Foundry chat.
 *
 * Payload shape (from PlayerInputDispatcher):
 *   { actor_id, user_id, narration, dialogue: [{npc_id, text, tone}], commands_ok, whisper_to }
 */
function escapeHTML(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export async function renderNarration(payload) {
  const narration = (payload?.narration || "").trim();
  const dialogue = Array.isArray(payload?.dialogue) ? payload.dialogue : [];

  const messages = [];

  if (narration) {
    const html = `<div class="ai-dm-narration">${escapeHTML(narration).replace(/\n/g, "<br>")}</div>`;
    const msg = {
      speaker: { alias: "DM" },
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

  for (const m of messages) {
    try {
      await ChatMessage.create(m);
    } catch (err) {
      console.warn("AI DM Bridge: ChatMessage.create failed", err, m);
    }
  }
}

