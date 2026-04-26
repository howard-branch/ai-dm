/**
 * Persistent, in-Foundry narration log window.
 *
 * The default Foundry chat sidebar is fine, but when players have it
 * collapsed or auto-hidden by the UI, narration scrolls past and is
 * effectively lost. This module provides a dedicated, dockable window
 * that *only* contains the AI DM narrative output and never auto-hides
 * — it's a permanent transcript of the session, in-game.
 *
 * Public API:
 *   NarrationLog.append({ kind, speaker, html, ts? })
 *   NarrationLog.show() / hide() / toggle()
 *   NarrationLog.clear()
 *
 * Wiring:
 *   - ``narration_renderer.js`` calls ``NarrationLog.append(...)``
 *     on every rendered chat message so the log stays in sync.
 *   - A scene-controls toolbar button toggles visibility.
 *   - The window restores its open/closed state across reloads via
 *     ``game.settings`` (client-scope).
 */

const SETTING_NS = "ai-dm-bridge";
const SETTING_OPEN = "narrationLogOpen";
const SETTING_ENTRIES = "narrationLogEntries";
const MAX_ENTRIES = 500;

let _instance = null;
let _entries = []; // {ts, kind, speaker, html}

// ------------------------------------------------------------------ //

function _persist() {
  try {
    game.settings.set(SETTING_NS, SETTING_ENTRIES, JSON.stringify(_entries));
  } catch (err) { /* ignore quota / not-registered */ }
}

function _restore() {
  try {
    const raw = game.settings.get(SETTING_NS, SETTING_ENTRIES);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) _entries = parsed.slice(-MAX_ENTRIES);
  } catch (err) { /* ignore */ }
}

// ------------------------------------------------------------------ //
// Application v2 if available, otherwise classic Application.
// We hand-roll the HTML so we don't need a Handlebars template file.

const _Base =
  foundry?.applications?.api?.ApplicationV2
    ?? Application;

class _NarrationLogApp extends _Base {
  constructor(...args) {
    super(...args);
    this._scrolledToBottom = true;
  }

  static DEFAULT_OPTIONS = {
    id: "ai-dm-narration-log",
    tag: "section",
    window: {
      title: "AI DM — Narration Log",
      icon: "fas fa-scroll",
      resizable: true,
      minimizable: true,
    },
    position: { width: 460, height: 520 },
  };

  // Classic-Application fallback options.
  static get defaultOptions() {
    return foundry.utils.mergeObject(super.defaultOptions ?? {}, {
      id: "ai-dm-narration-log",
      title: "AI DM — Narration Log",
      template: null,
      width: 460,
      height: 520,
      resizable: true,
      minimizable: true,
      classes: ["ai-dm-narration-log"],
    });
  }

  // ApplicationV2 render hook.
  async _renderHTML() {
    return _renderEntriesHTML(_entries);
  }
  async _replaceHTML(result, content) {
    content.innerHTML = result;
    this._afterRender(content);
  }
  // Classic Application render hook.
  async getData() { return {}; }
  async _renderInner() {
    const $el = $(`<div class="ai-dm-narration-log-body">${_renderEntriesHTML(_entries)}</div>`);
    return $el;
  }
  activateListeners(html) {
    super.activateListeners?.(html);
    const root = html?.[0] ?? html;
    this._afterRender(root);
  }

  _afterRender(root) {
    // Auto-scroll to bottom unless the user has scrolled up.
    const scroller = root.querySelector?.(".ai-dm-narration-log-body")
        ?? root;
    if (!scroller) return;
    if (this._scrolledToBottom) {
      scroller.scrollTop = scroller.scrollHeight;
    }
    scroller.addEventListener("scroll", () => {
      const atBottom =
        scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 8;
      this._scrolledToBottom = atBottom;
    }, { passive: true });
  }

  async close(options) {
    try { game.settings.set(SETTING_NS, SETTING_OPEN, false); } catch {}
    return super.close(options);
  }
}

function _renderEntriesHTML(entries) {
  if (!entries.length) {
    return `<div class="ai-dm-narration-log-empty">
      <em>No narration yet — type <code>/act look around</code> to begin.</em>
      <br><br>
      <small>If this window closes you can reopen it with
      <code>/log</code> in chat, the scroll icon in the scene-controls
      toolbar, or <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>L</kbd>.</small>
    </div>`;
  }
  const lines = entries.map((e) => {
    const t = new Date(e.ts).toLocaleTimeString();
    const sp = e.speaker ? `<strong>${_escape(e.speaker)}</strong>` : "";
    const sep = e.speaker ? ":" : "";
    return `<div class="ai-dm-narration-log-entry ai-dm-kind-${e.kind || "narration"}">
      <span class="ai-dm-narration-log-ts">[${t}]</span>
      ${sp}${sep}
      <span class="ai-dm-narration-log-body-html">${e.html}</span>
    </div>`;
  });
  return `<div class="ai-dm-narration-log-body">${lines.join("")}</div>`;
}

function _escape(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ------------------------------------------------------------------ //
// Public API

export const NarrationLog = {
  init() {
    // Settings (client-scope so per-player open/close + per-player log).
    try {
      game.settings.register(SETTING_NS, SETTING_OPEN, {
        scope: "client", config: false, type: Boolean, default: true,
      });
      game.settings.register(SETTING_NS, SETTING_ENTRIES, {
        scope: "client", config: false, type: String, default: "[]",
      });
    } catch (err) {
      console.warn("AI DM Bridge: NarrationLog settings register failed", err);
    }
    _restore();
    // Auto-open if the user had it open last time.
    let wasOpen = true;
    try { wasOpen = game.settings.get(SETTING_NS, SETTING_OPEN); } catch {}
    if (wasOpen) this.show();
  },

  app() {
    if (!_instance) _instance = new _NarrationLogApp();
    return _instance;
  },

  show() {
    try { game.settings.set(SETTING_NS, SETTING_OPEN, true); } catch {}
    this.app().render(true);
  },

  hide() {
    try { game.settings.set(SETTING_NS, SETTING_OPEN, false); } catch {}
    if (_instance?.rendered) _instance.close();
  },

  toggle() {
    if (_instance?.rendered) this.hide(); else this.show();
  },

  append(entry) {
    const e = {
      ts: entry?.ts ?? Date.now(),
      kind: entry?.kind || "narration",
      speaker: entry?.speaker || "",
      html: entry?.html || "",
    };
    _entries.push(e);
    if (_entries.length > MAX_ENTRIES) _entries = _entries.slice(-MAX_ENTRIES);
    _persist();
    if (_instance?.rendered) _instance.render(false);
  },

  clear() {
    _entries = [];
    _persist();
    if (_instance?.rendered) _instance.render(false);
  },
};

// Toolbar button (Scene Controls).
Hooks.on("getSceneControlButtons", (controls) => {
  const tools = (controls.find?.((c) => c.name === "token") ?? controls[0])?.tools;
  if (!tools) return;
  if (tools.find?.((t) => t.name === "ai-dm-narration-log")) return;
  tools.push({
    name: "ai-dm-narration-log",
    title: "AI DM Narration Log",
    icon: "fas fa-scroll",
    visible: true,
    button: true,
    onClick: () => NarrationLog.toggle(),
  });
});

Hooks.once("ready", () => {
  NarrationLog.init();

  // Expose a global handle so anyone (macros, browser console, other
  // modules) can re-open the log without hunting for the toolbar
  // button. Usage from console:
  //   game.aidm.log.show()   /   game.aidm.log.toggle()
  try {
    game.aidm = game.aidm || {};
    game.aidm.log = NarrationLog;
  } catch (err) { /* ignore */ }
});

// Keyboard shortcut: Ctrl/Cmd + Shift + L toggles the log. Registered
// through Foundry's keybinding API so the user can rebind it from
// Configure Controls if they prefer something else.
Hooks.once("init", () => {
  try {
    game.keybindings.register("ai-dm-bridge", "toggleNarrationLog", {
      name: "Toggle AI DM Narration Log",
      hint: "Show or hide the persistent AI DM transcript window.",
      editable: [{ key: "KeyL", modifiers: ["Control", "Shift"] }],
      onDown: () => { NarrationLog.toggle(); return true; },
      restricted: false,
    });
  } catch (err) {
    console.warn("AI DM Bridge: keybinding register failed", err);
  }
});

// Chat command: typing `/aidm log`, `/aidm-log`, `/dmlog`, or just
// `/log` (when followed by show/hide/toggle) opens the window. We
// intercept on `chatMessage` and swallow the input so Foundry doesn't
// send it as a real chat message.
Hooks.on("chatMessage", (_chatLog, message, _data) => {
  const m = String(message || "").trim().toLowerCase();
  if (!m) return true;
  const isDmLog =
    m === "/aidm-log" ||
    m === "/dmlog" ||
    m === "/narration" ||
    m === "/log" ||
    m.startsWith("/aidm log") ||
    m.startsWith("/aidm-log ") ||
    m.startsWith("/dmlog ") ||
    m.startsWith("/log ");
  if (!isDmLog) return true;
  const arg = m.split(/\s+/).slice(1).join(" ").trim();
  if (arg === "hide" || arg === "close") NarrationLog.hide();
  else if (arg === "clear") NarrationLog.clear();
  else if (arg === "toggle") NarrationLog.toggle();
  else NarrationLog.show();
  return false; // swallow — don't post to chat
});

