/**
 * Phase 2: tiny side panel for chat-driven turns.
 *
 * Reuses the same `player_input` / `player_intent` channels the
 * /act chat command uses. Quick actions:
 *   - Talk    → /act prefix "say "
 *   - Inspect → /act prefix "inspect "
 *   - Move    → /act prefix "move to "
 *   - End Turn → structured intent (combat machine)
 */
import { sendPlayerInput, sendStructuredIntent } from "./socket_bridge.js";

const TEMPLATE = "modules/ai-dm-bridge/templates/dm_panel.hbs";

function activeActor() {
  const tok = canvas?.tokens?.controlled?.[0];
  if (tok?.actor) return tok.actor;
  if (game.user?.character) return game.user.character;
  const owned = game.actors?.filter((a) => a.testUserPermission(game.user, "OWNER")) || [];
  return owned[0] || null;
}

class DMPanel extends Application {
  static get defaultOptions() {
    return foundry.utils.mergeObject(super.defaultOptions, {
      id: "ai-dm-panel",
      title: "AI DM",
      template: TEMPLATE,
      width: 360,
      height: "auto",
      resizable: true,
      classes: ["ai-dm-bridge"],
    });
  }

  getData() {
    const actor = activeActor();
    return {
      actorName: actor?.name || "(no actor)",
      actorId: actor?.id || null,
    };
  }

  activateListeners(html) {
    super.activateListeners(html);
    html.find("form.ai-dm-form").on("submit", (ev) => {
      ev.preventDefault();
      const text = html.find('textarea[name="text"]').val();
      this._send(String(text || "").trim());
      html.find('textarea[name="text"]').val("");
    });
    html.find('button[data-action="talk"]').on("click", () => this._prefix("say "));
    html.find('button[data-action="inspect"]').on("click", () => this._prefix("inspect "));
    html.find('button[data-action="move"]').on("click", () => this._prefix("move to "));
    html.find('button[data-action="end_turn"]').on("click", () => this._endTurn());
  }

  _prefix(prefix) {
    const ta = this.element.find('textarea[name="text"]');
    const current = String(ta.val() || "");
    ta.val(prefix + current);
    ta.focus();
  }

  _send(text) {
    if (!text) return;
    const actor = activeActor();
    if (!actor) {
      ui.notifications?.warn("No actor selected.");
      return;
    }
    sendPlayerInput({
      user_id: game.user.id,
      user_name: game.user.name,
      actor_id: actor.id,
      actor_name: actor.name,
      scene_id: canvas?.scene?.id ?? null,
      text,
    });
    ChatMessage.create({
      speaker: { alias: actor.name },
      content: `<em>${text}</em>`,
    });
  }

  _endTurn() {
    const actor = activeActor();
    if (!actor) return;
    sendStructuredIntent({
      type: "end_turn",
      actor_id: actor.id,
      scene_id: canvas?.scene?.id ?? null,
      user_id: game.user.id,
    });
  }
}

let _panelInstance = null;

function togglePanel() {
  if (!_panelInstance) _panelInstance = new DMPanel();
  if (_panelInstance.rendered) _panelInstance.close();
  else _panelInstance.render(true);
}

Hooks.on("getSceneControlButtons", (controls) => {
  const tokens = controls.find?.((c) => c.name === "token");
  if (!tokens) return;
  tokens.tools.push({
    name: "ai-dm-panel",
    title: "AI DM Panel",
    icon: "fas fa-comment-dots",
    button: true,
    onClick: () => togglePanel(),
  });
});

Hooks.once("ready", () => {
  // Expose for macros / debugging.
  globalThis.AIDMBridge = globalThis.AIDMBridge || {};
  globalThis.AIDMBridge.openPanel = togglePanel;
});

