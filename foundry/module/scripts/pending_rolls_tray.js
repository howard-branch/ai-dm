/**
 * Persistent floating tray for outstanding roll requests.
 *
 * The Foundry chat sidebar can scroll past a roll prompt in seconds
 * (or hide it entirely when the sidebar is collapsed and the toast
 * notification fades). This tray surfaces every *unresolved* roll
 * request as a sticky card with a Roll button that stays visible
 * until either the player rolls it or the Python-side dispatcher
 * auto-rolls it on timeout (configured by `rolls.timeout_s`).
 *
 * Public API:
 *   PendingRollsTray.add(payload)         — show a card for the request
 *   PendingRollsTray.remove(requestId)    — drop the card (resolved/cancelled)
 *   PendingRollsTray.show()/hide()/toggle()
 *   PendingRollsTray.has(requestId)
 *
 * The tray is purely *local* to each client. The roll-result lifecycle
 * (chat update, `player_roll_resolved` POST, `chat_roll` echo on
 * auto-roll) is identical to what the chat-card path does — see
 * `roll_prompt.js::performRoll` which both code paths share.
 */

const SETTING_NS = "ai-dm-bridge";
const SETTING_OPEN = "pendingRollsTrayOpen";

let _instance = null;
let _entries = new Map(); // request_id -> { payload, addedAt }

// ------------------------------------------------------------------ //
// Window class. Uses ApplicationV2 when available, else classic.

const _Base =
  foundry?.applications?.api?.ApplicationV2 ?? Application;

class _PendingRollsTrayApp extends _Base {
  static DEFAULT_OPTIONS = {
    id: "ai-dm-pending-rolls",
    tag: "section",
    window: {
      title: "AI DM — Pending Rolls",
      icon: "fas fa-dice-d20",
      resizable: true,
      minimizable: true,
    },
    position: { width: 340, height: "auto", top: 80, left: 120 },
  };

  static get defaultOptions() {
    return foundry.utils.mergeObject(super.defaultOptions ?? {}, {
      id: "ai-dm-pending-rolls",
      title: "AI DM — Pending Rolls",
      template: null,
      width: 340,
      height: "auto",
      resizable: true,
      minimizable: true,
      classes: ["ai-dm-pending-rolls"],
    });
  }

  // ApplicationV2 hooks ---------------------------------------------- //
  async _renderHTML() { return _renderEntriesHTML(); }
  async _replaceHTML(result, content) {
    content.innerHTML = result;
    this._wire(content);
  }

  // Classic Application hooks ---------------------------------------- //
  async getData() { return {}; }
  async _renderInner() {
    const $el = $(`<div class="ai-dm-pending-rolls-body">${_renderEntriesHTML()}</div>`);
    return $el;
  }
  activateListeners(html) {
    super.activateListeners?.(html);
    const root = html?.[0] ?? html;
    this._wire(root);
  }

  _wire(root) {
    if (!root) return;
    root.querySelectorAll("button.aidm-tray-roll-btn").forEach((btn) => {
      btn.addEventListener("click", async (ev) => {
        ev.preventDefault();
        const rid = btn.getAttribute("data-request-id");
        const entry = _entries.get(rid);
        if (!entry) return;
        // Disable so a frantic double-click doesn't double-resolve.
        btn.disabled = true;
        btn.textContent = "Rolling…";
        try {
          const { performRoll } = await import("./roll_prompt.js");
          await performRoll(entry.payload);
        } catch (err) {
          console.warn("AI DM Bridge: tray roll failed", err);
          btn.disabled = false;
          btn.textContent = "🎲 Roll";
          return;
        }
        // performRoll is responsible for calling .remove(rid).
      });
    });
  }

  async close(options) {
    try { game.settings.set(SETTING_NS, SETTING_OPEN, false); } catch {}
    return super.close(options);
  }
}

function _escape(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function _resolveActorName(actorId) {
  if (!actorId) return "the party";
  const a = game.actors?.get(actorId)
    ?? game.actors?.find((x) => (x.name || "").toLowerCase() === String(actorId).toLowerCase());
  return a?.name || actorId;
}

function _renderEntriesHTML() {
  if (_entries.size === 0) {
    return `<div class="ai-dm-pending-rolls-empty">
      <em>No pending rolls.</em><br>
      <small>This window stays out of the way and only lights up
      when the AI DM asks you to roll something.</small>
    </div>`;
  }
  const cards = [];
  for (const [rid, { payload }] of _entries.entries()) {
    const actorName = _escape(_resolveActorName(payload.actor_id));
    const prompt = _escape(payload.prompt_text || "Make a roll");
    const dc = (payload.dc != null)
      ? `<span class="aidm-tray-dc">DC ${_escape(payload.dc)}</span>` : "";
    const adv = String(payload.advantage || "normal").toLowerCase();
    const advBadge = adv === "advantage"
        ? ` <span class="aidm-tray-adv">[adv]</span>`
        : adv === "disadvantage"
            ? ` <span class="aidm-tray-dis">[dis]</span>`
            : "";
    const tag = `${_escape(payload.roll_type || "roll")}${
      payload.key ? "/" + _escape(payload.key) : ""
    }`;
    cards.push(`
      <div class="aidm-tray-card" data-request-id="${_escape(rid)}">
        <div class="aidm-tray-actor">
          <strong>${actorName}</strong>
          <span class="aidm-tray-tag">[${tag}]</span>
        </div>
        <div class="aidm-tray-prompt">${prompt} ${dc}${advBadge}</div>
        <button type="button" class="aidm-tray-roll-btn"
                data-request-id="${_escape(rid)}">🎲 Roll</button>
      </div>`);
  }
  return `<div class="ai-dm-pending-rolls-body">${cards.join("")}</div>`;
}

// ------------------------------------------------------------------ //
// Public API.

export const PendingRollsTray = {
  init() {
    try {
      game.settings.register(SETTING_NS, SETTING_OPEN, {
        scope: "client", config: false, type: Boolean, default: true,
      });
    } catch (err) { /* ignore */ }
  },

  app() {
    if (!_instance) _instance = new _PendingRollsTrayApp();
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

  has(requestId) { return _entries.has(String(requestId)); },

  add(payload) {
    if (!payload?.request_id) return;
    const rid = String(payload.request_id);
    _entries.set(rid, { payload, addedAt: Date.now() });
    // Auto-show when a new roll arrives so the player notices it
    // even if they previously closed the tray.
    this.show();
    if (_instance?.rendered) _instance.render(false);
  },

  remove(requestId) {
    if (!requestId) return;
    const rid = String(requestId);
    if (!_entries.delete(rid)) return;
    if (_instance?.rendered) _instance.render(false);
  },

  clear() {
    _entries.clear();
    if (_instance?.rendered) _instance.render(false);
  },
};

Hooks.once("ready", () => {
  PendingRollsTray.init();
  try {
    game.aidm = game.aidm || {};
    game.aidm.pendingRolls = PendingRollsTray;
  } catch (err) { /* ignore */ }

  // Mirror the chat-card resolution back into the tray: when another
  // client (or this one) updates the rollRequest flag to "resolved",
  // drop the matching tray entry on every client.
  Hooks.on("updateChatMessage", (message) => {
    try {
      const flag = message.getFlag?.("ai-dm-bridge", "rollRequest");
      if (flag && flag.state === "resolved" && flag.requestId) {
        PendingRollsTray.remove(flag.requestId);
      }
    } catch (err) { /* ignore */ }
  });
});

