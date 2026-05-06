/**
 * Lightweight monster sheet for AI-DM-spawned NPCs.
 *
 * Shows just the three things a chat-driven DM workflow needs at a
 * glance: name, current/max HP, and creature type (with size + CR if
 * present). The full dnd5e NPC sheet is overkill for the
 * "what is this thing and how close is it to dying?" question that
 * comes up every NPC turn, so we register a header button on the
 * Token HUD that pops this up instead.
 *
 * Triggers:
 *   - Token HUD button (shield icon) on any NPC token.
 *   - Macro entrypoint: ``AIDMBridge.openMonsterSheet(actor)``.
 */

const TEMPLATE = "modules/ai-dm-bridge/templates/monster_sheet.hbs";

function _readType(actor) {
  const t = actor?.system?.details?.type;
  if (!t) return null;
  if (typeof t === "string") return t;
  const parts = [t.value, t.subtype].filter((x) => x && String(x).trim());
  if (t.custom) parts.push(String(t.custom));
  return parts.length ? parts.join(" / ") : null;
}

function _readSize(actor) {
  const code = actor?.system?.traits?.size;
  if (!code) return null;
  const map = { tiny: "Tiny", sm: "Small", med: "Medium",
                lg: "Large", huge: "Huge", grg: "Gargantuan" };
  return map[code] || code;
}

function _readCR(actor) {
  const cr = actor?.system?.details?.cr;
  if (cr === null || cr === undefined || cr === "") return null;
  if (cr === 0.125) return "1/8";
  if (cr === 0.25)  return "1/4";
  if (cr === 0.5)   return "1/2";
  return String(cr);
}

class MonsterSheet extends Application {
  constructor(actor, options = {}) {
    super(options);
    this.actor = actor;
    this._hookId = Hooks.on("updateActor", (a) => {
      if (a?.id === this.actor?.id) this.render(false);
    });
  }

  static get defaultOptions() {
    return foundry.utils.mergeObject(super.defaultOptions, {
      id: "ai-dm-monster-sheet",
      classes: ["ai-dm-bridge", "ai-dm-monster-sheet-app"],
      template: TEMPLATE,
      width: 320,
      height: "auto",
      resizable: false,
      popOut: true,
    });
  }

  get title() {
    return this.actor?.name ? `Monster — ${this.actor.name}` : "Monster";
  }

  getData() {
    const a = this.actor;
    const hpVal = Number(a?.system?.attributes?.hp?.value ?? 0);
    const hpMax = Number(a?.system?.attributes?.hp?.max ?? 0) || 1;
    const pct = Math.max(0, Math.min(100, Math.round((hpVal / hpMax) * 100)));
    return {
      name: a?.name || "Unknown",
      img: a?.img || "icons/svg/mystery-man.svg",
      creatureType: _readType(a),
      size: _readSize(a),
      alignment: a?.system?.details?.alignment || null,
      hp: { value: hpVal, max: hpMax, percent: pct },
      ac: a?.system?.attributes?.ac?.value
          ?? a?.system?.attributes?.ac?.flat
          ?? null,
      cr: _readCR(a),
      xp: a?.system?.details?.xp?.value ?? null,
    };
  }

  activateListeners(html) {
    super.activateListeners(html);
    html.find('[data-action="close"]').on("click", () => this.close());
  }

  async close(options) {
    if (this._hookId) {
      Hooks.off("updateActor", this._hookId);
      this._hookId = null;
    }
    return super.close(options);
  }
}

const _openSheets = new Map();

function openMonsterSheet(actor) {
  if (!actor) {
    ui.notifications?.warn("No actor for monster sheet.");
    return null;
  }
  const existing = _openSheets.get(actor.id);
  if (existing && existing.rendered) {
    existing.bringToTop?.();
    return existing;
  }
  const sheet = new MonsterSheet(actor);
  _openSheets.set(actor.id, sheet);
  sheet.render(true);
  return sheet;
}

function _isNpc(actor) {
  if (!actor) return false;
  if (actor.type === "npc") return true;
  // Belt-and-braces for non-dnd5e systems.
  return actor.type !== "character";
}

Hooks.on("renderTokenHUD", (hud, html, data) => {
  try {
    const tokenDoc = hud?.object?.document ?? canvas?.tokens?.get(data?._id)?.document;
    const actor = tokenDoc?.actor ?? game.actors?.get(data?.actorId);
    if (!_isNpc(actor)) return;
    const btn = $(
      '<div class="control-icon ai-dm-monster-hud" title="AI-DM Monster Sheet">' +
        '<i class="fas fa-dragon"></i>' +
      "</div>",
    );
    btn.on("click", (ev) => {
      ev.preventDefault();
      openMonsterSheet(actor);
    });
    html.find(".col.left").append(btn);
  } catch (err) {
    console.warn("ai-dm: monster_sheet HUD button failed", err);
  }
});

Hooks.once("ready", () => {
  globalThis.AIDMBridge = globalThis.AIDMBridge || {};
  globalThis.AIDMBridge.openMonsterSheet = openMonsterSheet;
});

export { MonsterSheet, openMonsterSheet };

