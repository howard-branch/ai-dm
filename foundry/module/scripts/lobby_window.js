/**
 * Persistent lobby window.
 *
 * Foundry's chat-bubble overlay fades out after a few seconds and the
 * sidebar can be collapsed, so the original chat-only lobby status was
 * easy to miss entirely. This window is a sticky panel that the GM can't
 * lose: it pops open on every inbound `lobby_status`, stays on screen
 * until `/startgame` is fired (or the AI DM auto-starts), and includes
 * a single big "Start Game" button so the GM doesn't have to remember
 * the slash command.
 *
 * Public API:
 *   LobbyWindow.update(payload)  // append/replace status, auto-show
 *   LobbyWindow.markStarted()    // hide on /startgame
 *   LobbyWindow.show() / hide() / toggle()
 */

let _instance = null;
let _lastPayload = null;
let _started = false;

const _Base =
  foundry?.applications?.api?.ApplicationV2
    ?? Application;

class _LobbyApp extends _Base {
  static DEFAULT_OPTIONS = {
    id: "ai-dm-lobby",
    tag: "section",
    window: {
      title: "AI DM — Lobby",
      icon: "fas fa-users",
      resizable: true,
      minimizable: true,
    },
    position: { width: 380, height: "auto" },
  };

  static get defaultOptions() {
    return foundry.utils.mergeObject(super.defaultOptions ?? {}, {
      id: "ai-dm-lobby",
      title: "AI DM — Lobby",
      template: null,
      width: 380,
      height: "auto",
      resizable: true,
      minimizable: true,
      classes: ["ai-dm-lobby"],
    });
  }

  // ApplicationV2 render hooks.
  async _renderHTML() { return _renderHTML(_lastPayload); }
  async _replaceHTML(result, content) {
    content.innerHTML = result;
    this._wireButtons(content);
  }
  // Classic Application fallback.
  async getData() { return {}; }
  async _renderInner() {
    const $el = $(`<div class="ai-dm-lobby-body">${_renderHTML(_lastPayload)}</div>`);
    return $el;
  }
  activateListeners(html) {
    super.activateListeners?.(html);
    this._wireButtons(html?.[0] ?? html);
  }

  _wireButtons(root) {
    if (!root) return;
    const startBtn = root.querySelector?.(".ai-dm-lobby-start");
    if (startBtn) {
      startBtn.addEventListener("click", () => _onStartClicked());
    }
    const refreshBtn = root.querySelector?.(".ai-dm-lobby-refresh");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        try { ui.notifications?.info("AI DM: refresh request — Python will repush within 30s."); } catch {}
      });
    }
  }
}

function _escape(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function _renderHTML(payload) {
  if (!payload) {
    return `<div class="ai-dm-lobby-empty"><em>Waiting for first lobby status…</em></div>`;
  }
  const expected = Array.isArray(payload.expected) ? payload.expected : [];
  const connected = Array.isArray(payload.connected) ? payload.connected : [];
  const missing = Array.isArray(payload.missing) ? payload.missing : [];
  const ready = !!payload.ready;
  const gmPresent = !!payload.gm_present;
  const dot = (ok) => (ok ? "🟢" : "⚪");

  const rows = [];
  rows.push(`<div class="ai-dm-lobby-title">🎲 ${_escape(payload.campaign || "AI DM")}</div>`);
  rows.push(`<div class="ai-dm-lobby-row">${dot(gmPresent)} GM ${gmPresent ? "connected" : "<em>not connected</em>"}</div>`);

  if (expected.length) {
    rows.push(`<div class="ai-dm-lobby-section">Expected players</div>`);
    for (const name of expected) {
      const here = connected.includes(name);
      rows.push(`<div class="ai-dm-lobby-row">${dot(here)} ${_escape(name)}</div>`);
    }
  } else if (connected.length) {
    rows.push(`<div class="ai-dm-lobby-section">Connected</div>`);
    for (const name of connected) {
      rows.push(`<div class="ai-dm-lobby-row">${dot(true)} ${_escape(name)}</div>`);
    }
  }

  if (missing.length) {
    rows.push(`<div class="ai-dm-lobby-missing">Waiting for: ${missing.map(_escape).join(", ")}</div>`);
  }

  if (game?.user?.isGM) {
    rows.push(
      `<div class="ai-dm-lobby-actions">` +
        `<button type="button" class="ai-dm-lobby-start" ${ready ? "" : ""}>` +
          (ready ? "▶ Start Game" : "▶ Start Anyway") +
        `</button>` +
      `</div>`
    );
    rows.push(
      `<div class="ai-dm-lobby-hint">` +
        `or type <code>/startgame</code> in chat` +
      `</div>`
    );
  } else {
    rows.push(
      `<div class="ai-dm-lobby-hint">${_escape(payload.instructions || "Waiting for the GM to start the game.")}</div>`
    );
  }

  return `<div class="ai-dm-lobby-body">${rows.join("")}</div>`;
}

function _onStartClicked() {
  // Fire `/startgame` via the same path the chat command uses, so all
  // the same downstream wiring runs.
  try {
    if (!game?.user?.isGM) {
      ui.notifications?.warn("Only the GM can start the game.");
      return;
    }
    // chat_commands.js exposes a global send when the bridge is up.
    const send = globalThis.AIDMBridge?.sendEvent;
    if (send) {
      send("start_game", {
        user_id: game.user.id,
        user_name: game.user.name,
        clicked_in_lobby: true,
      });
    } else {
      // Fallback: emit /startgame as a chat message so the existing
      // chat-command handler picks it up.
      ChatMessage.create({
        speaker: ChatMessage.getSpeaker(),
        content: "/startgame",
      });
    }
    LobbyWindow.markStarted();
  } catch (err) {
    console.warn("AI DM Bridge: lobby start button failed", err);
    ui.notifications?.error(`Lobby start failed: ${err}`);
  }
}

// ------------------------------------------------------------------ //

export const LobbyWindow = {
  app() {
    if (!_instance) _instance = new _LobbyApp();
    return _instance;
  },

  update(payload) {
    _lastPayload = payload;
    if (_started) return;             // game already running — don't pop
    this.app().render(true);
  },

  show() {
    if (_started) return;
    this.app().render(true);
  },

  hide() {
    if (_instance?.rendered) _instance.close();
  },

  toggle() {
    if (_instance?.rendered) this.hide(); else this.show();
  },

  markStarted() {
    _started = true;
    this.hide();
  },

  isStarted() { return _started; },
};

// Hide the lobby window automatically once Python publishes that the
// game has begun — this fires when /startgame is processed (the
// inbound `start_game` ack is the existing `foundry.start_game` event
// path; we listen for an outbound echo via Hooks.on).
Hooks.once("ready", () => {
  try {
    game.aidm = game.aidm || {};
    game.aidm.lobby = LobbyWindow;
  } catch { /* ignore */ }
});

