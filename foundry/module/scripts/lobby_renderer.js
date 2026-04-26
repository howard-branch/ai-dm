/**
 * Render an inbound {event:"lobby_status"} payload as a GM-only chat
 * message. Tells the GM who has joined, who is still missing, and how
 * to start the game.
 *
 * Payload shape (from src/ai_dm/app/lobby.py):
 *   {
 *     campaign, scene_id,
 *     gm_present:   bool,
 *     connected:    [user_name, ...],
 *     missing:      [user_name, ...],   // empty if no expected_players
 *     expected:     [user_name, ...],
 *     ready:        bool,
 *     auto_grace:   number,
 *     instructions: string
 *   }
 */
function escapeHTML(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export async function renderLobbyStatus(payload) {
  // Stash for /lobby chat command.
  globalThis.AIDMBridge = globalThis.AIDMBridge || {};
  globalThis.AIDMBridge.lastLobbyStatus = payload;

  // Render in every browser tab — but whisper to GMs only so players
  // don't see the bookkeeping. (Players still get the opening narration
  // once the GM types /startgame.)
  const gmIds = (game.users?.filter?.((u) => u.isGM) || []).map((u) => u.id);
  if (gmIds.length === 0) return;

  const connected = Array.isArray(payload?.connected) ? payload.connected : [];
  const missing = Array.isArray(payload?.missing) ? payload.missing : [];
  const expected = Array.isArray(payload?.expected) ? payload.expected : [];
  const gmPresent = !!payload?.gm_present;
  const ready = !!payload?.ready;

  const dot = (ok) => (ok ? "🟢" : "⚪");
  const lines = [];

  lines.push(
    `<div style="font-weight:bold;margin-bottom:0.3em">` +
    `🎲 ${escapeHTML(payload?.campaign || "AI DM")} — Lobby` +
    `</div>`
  );
  lines.push(`<div>${dot(gmPresent)} GM ${gmPresent ? "connected" : "not connected"}</div>`);

  if (expected.length > 0) {
    lines.push(`<div style="margin-top:0.2em"><em>Expected players:</em></div>`);
    for (const name of expected) {
      const here = connected.includes(name);
      lines.push(`<div>${dot(here)} ${escapeHTML(name)}</div>`);
    }
  } else if (connected.length > 0) {
    lines.push(`<div style="margin-top:0.2em"><em>Connected:</em> ${connected.map(escapeHTML).join(", ")}</div>`);
  }

  if (missing.length > 0) {
    lines.push(
      `<div style="margin-top:0.2em;color:#a55"><em>Waiting for:</em> ${missing.map(escapeHTML).join(", ")}</div>`
    );
  }

  lines.push(
    `<div style="margin-top:0.4em;border-top:1px solid #888;padding-top:0.3em">` +
    (ready
      ? `✅ Ready — type <code>/startgame</code> to begin.`
      : `<em>${escapeHTML(payload?.instructions || "Type /startgame to begin when ready.")}</em>`) +
    `</div>`
  );

  try {
    await ChatMessage.create({
      speaker: { alias: "AI DM Lobby" },
      content: lines.join(""),
      whisper: gmIds,
    });
  } catch (err) {
    console.warn("AI DM Bridge: lobby_status ChatMessage.create failed", err);
  }

  // Also append to the persistent narration log so the lobby status
  // doesn't vanish when Foundry's chat-bubble overlay times out or the
  // sidebar is collapsed. The log auto-opens here so a GM who closed
  // it sees the lobby reappear as soon as a new status arrives.
  try {
    const { NarrationLog } = await import("./narration_log.js");
    NarrationLog.append({
      kind: ready ? "opening" : "lobby",
      speaker: "AI DM Lobby",
      html: lines.join(""),
    });
    if (!ready) NarrationLog.show();
  } catch (err) {
    console.warn("AI DM Bridge: lobby_status NarrationLog append failed", err);
  }

  // Sticky lobby window — the *primary* lobby UI. Pops up on every
  // status, has a big "Start Game" button, and stays put until the GM
  // starts the game (or AI_DM_AUTOSTART is set on the Python side).
  try {
    const { LobbyWindow } = await import("./lobby_window.js");
    LobbyWindow.update(payload);
  } catch (err) {
    console.warn("AI DM Bridge: lobby_status LobbyWindow update failed", err);
  }
}

