/**
 * Phase 1: chat-driven AI DM command.
 *
 * Sends an in-character action to the AI DM. Three input forms work:
 *
 *   ! <text>      — bang form. Anything starting with "!" is sent
 *                   to the AI. Recommended (shortest, works on every
 *                   Foundry version because "!" doesn't trigger
 *                   Foundry's slash-command parser).
 *   /act <text>   — slash form. Works on V14+ via ChatLog.CHAT_COMMANDS,
 *                   and on V12/V13 via the chatMessage hook. Kept for
 *                   discoverability.
 *   AIDMBridge.act("<text>") — programmatic form for macros.
 *
 * Per-user actor pinning (so the GM doesn't get auto-assigned to the
 * first actor in the world):
 *
 *   !iam <name|id>  — pin the actor used by future ! / /act commands.
 *   !whoami         — show which actor is currently pinned.
 *   AIDMBridge.setMyActor("Bran the Brave")  — programmatic equivalent.
 *
 * Or use the module setting "AI DM: My Actor" under
 * Game Settings → Configure Settings → AI DM Bridge.
 *
 * Resolution order when no token is selected:
 *   1. controlled token's actor
 *   2. !iam pin / module setting
 *   3. game.user.character (Foundry's built-in user→character link)
 *   4. first actor the user owns (the previous bug — GM owns
 *      everyone, so this picked the wrong PC)
 */
import { sendPlayerInput, sendEventToPython } from "./socket_bridge.js";

// Bang prefix: a single "!" followed by optional whitespace and a body.
// The negative lookahead "(?!!)" excludes "!!..." so dice/macro modules
// that use a doubled bang prefix are not hijacked.
const BANG_RE = /^!(?!!)\s*([\s\S]*)$/;
// Slash prefix (handled by chatMessage hook on V12/V13 and by
// CHAT_COMMANDS on V14+).
const SLASH_RE = /^\/act\b([\s\S]*)$/i;
// "!iam <name|id>" or "!whoami" — pin / inspect the actor used by /act.
const IAM_RE = /^!iam\b\s*([\s\S]*)$/i;
const WHOAMI_RE = /^!whoami\b/i;
// "/voice on|off|toggle|status" — flip host-side mic input.
const VOICE_RE = /^\/voice\b\s*([a-z]*)\s*$/i;
// "/startgame" — GM only; tells Python the lobby is ready.
const STARTGAME_RE = /^\/startgame\b/i;
// "/lobby" — print the most recent lobby status (cached locally).
const LOBBY_RE = /^\/lobby\b/i;

const MODULE_ID = "ai-dm-bridge";
const SETTING_MY_ACTOR = "myActor";

function _myPinnedActorId() {
  try {
    return game.settings?.get(MODULE_ID, SETTING_MY_ACTOR) || "";
  } catch {
    return "";
  }
}

function _setMyPinnedActor(actorId) {
  try {
    return game.settings?.set(MODULE_ID, SETTING_MY_ACTOR, actorId || "");
  } catch (err) {
    console.warn("AI DM Bridge: failed to persist my actor", err);
  }
}

function resolveActorForUser(user, speaker) {
  // 1. Use the speaker the message was created with (token-controlled).
  if (speaker?.actor) {
    const a = game.actors?.get(speaker.actor);
    if (a) return a;
  }
  // 2. Per-user pinned actor — set via the module setting or `!iam`.
  const pinned = _myPinnedActorId();
  if (pinned) {
    const a = game.actors?.get(pinned);
    if (a) return a;
  }
  // 3. The user's assigned default character (Foundry's built-in setting).
  if (user?.character) return user.character;
  // 3. Last resort: first actor the user owns.
  const owned = game.actors?.filter((a) => a.testUserPermission(user, "OWNER")) || [];
  return owned[0] || null;
}

/** Core handler. Returns true on success, false on user error. */
async function performAct(text, { suppressEcho = false } = {}) {
  text = (text || "").trim();
  if (!text) {
    ui.notifications?.warn("Usage: ! <what your character does or says>");
    return false;
  }

  const user = game.user;
  const speaker = ChatMessage.getSpeaker();
  const actor = resolveActorForUser(user, speaker);
  if (!actor) {
    ui.notifications?.warn(
      "/act: no actor assigned to your user. Drag a PC onto your user, or select a token you own."
    );
    return false;
  }

  const payload = {
    user_id: user.id,
    user_name: user.name,
    actor_id: actor.id,
    actor_name: actor.name,
    scene_id: canvas?.scene?.id ?? null,
    text,
  };
  console.log("AI DM Bridge: sending /act payload", payload);

  try {
    sendPlayerInput(payload);
  } catch (err) {
    console.warn("AI DM Bridge: /act failed to send", err);
    ui.notifications?.error(`/act failed: ${err}`);
    return false;
  }

  if (!suppressEcho) {
    try {
      await ChatMessage.create({
        speaker: { alias: actor.name },
        content: `<em>${text}</em>`,
      });
    } catch (err) {
      console.warn("AI DM Bridge: echo ChatMessage failed", err);
    }
  }
  return true;
}

// --------------------------------------------------------------------- //
// chatMessage hook — handles "!act" universally and "/act" on
// V12/V13. On V14 the slash parser fires before this hook for
// "/act", so the CHAT_COMMANDS registration below covers that case.
// --------------------------------------------------------------------- //

// --------------------------------------------------------------------- //
// chatMessage hook — kept for V12/V13 where it still fires before
// the chat parser. On V14+ this hook is no longer reliably called
// for "/"-prefixed input, but it still catches "!" input on most
// versions; the preCreateChatMessage hook below is the real
// universal catch-all.
// --------------------------------------------------------------------- //

function _stripHTML(s) {
  // V14 wraps user input in <p>...</p> before invoking chat hooks.
  // Strip tags so BANG_RE / SLASH_RE see the raw text the user typed.
  return String(s ?? "").replace(/<[^>]*>/g, "").trim();
}

// V14 fires BOTH chatMessage and preCreateChatMessage for the same
// input. Whichever runs first claims the message; the second one
// sees the flag and bails so we don't double-submit to Python.
let _justHandled = false;
function _claim() {
  if (_justHandled) return false;
  _justHandled = true;
  // Reset on the next macrotask — both hooks fire in the same tick.
  setTimeout(() => { _justHandled = false; }, 0);
  return true;
}

/**
 * Inspect a raw chat string and route to the right handler.
 * Returns true if we consumed the message (caller must suppress it),
 * false if the caller should let it through.
 */
function _routeChatInput(raw) {
  const trimmed = _stripHTML(raw);
  if (!trimmed) return false;

  // !whoami — print which actor will be used.
  if (WHOAMI_RE.test(trimmed)) {
    if (!_claim()) return true;
    const actor = resolveActorForUser(game.user, ChatMessage.getSpeaker());
    const pinned = _myPinnedActorId();
    const src = actor && pinned === actor.id ? "pinned" :
                actor && game.user?.character?.id === actor.id ? "user.character" :
                actor ? "first owned" : "none";
    ui.notifications?.info(
      actor ? `AI DM will speak as: ${actor.name} (${src})` : "AI DM has no actor for you yet."
    );
    return true;
  }

  // !iam <name|id> — pin the actor used by /act for this user.
  const iam = IAM_RE.exec(trimmed);
  if (iam) {
    if (!_claim()) return true;
    const arg = (iam[1] || "").trim();
    if (!arg) {
      ui.notifications?.warn("Usage: !iam <actor name or id>");
      return true;
    }
    let target = game.actors?.get(arg);
    if (!target) target = game.actors?.find((a) => a.name?.toLowerCase() === arg.toLowerCase());
    if (!target) {
      ui.notifications?.warn(`!iam: no actor matches "${arg}"`);
      return true;
    }
    _setMyPinnedActor(target.id);
    ui.notifications?.info(`AI DM will now speak as: ${target.name}`);
    return true;
  }

  // /startgame — GM only. Tells Python the lobby is ready.
  if (STARTGAME_RE.test(trimmed)) {
    if (!_claim()) return true;
    if (!game.user?.isGM) {
      ui.notifications?.warn("/startgame: only the GM can start the game.");
      return true;
    }
    try {
      sendEventToPython("start_game", {
        user_id: game.user.id,
        user_name: game.user.name,
      });
      ui.notifications?.info("AI DM: starting the game…");
      // Dismiss the sticky lobby window immediately.
      import("./lobby_window.js").then(({ LobbyWindow }) =>
        LobbyWindow.markStarted()
      ).catch(() => {});
    } catch (err) {
      console.warn("AI DM Bridge: /startgame failed to send", err);
      ui.notifications?.error(`/startgame failed: ${err}`);
    }
    return true;
  }

  // /lobby — show the most recent lobby status whisper.
  if (LOBBY_RE.test(trimmed)) {
    if (!_claim()) return true;
    const last = globalThis.AIDMBridge?.lastLobbyStatus;
    if (!last) {
      ui.notifications?.info("AI DM: no lobby status received yet.");
    } else {
      const missing = (last.missing || []).join(", ") || "(none)";
      const conn = (last.connected || []).join(", ") || "(none)";
      ui.notifications?.info(
        `Lobby — connected: ${conn} | missing: ${missing} | ` +
        `GM: ${last.gm_present ? "yes" : "no"} | ready: ${last.ready ? "yes" : "no"}`
      );
    }
    return true;
  }

  // /voice on|off|toggle|status — flip host-side mic input.
  const vm = VOICE_RE.exec(trimmed);  if (vm) {
    if (!_claim()) return true;
    const action = (vm[1] || "status").toLowerCase();
    if (!["on", "off", "toggle", "status", "start", "stop", "enable", "disable"].includes(action)) {
      ui.notifications?.warn("Usage: /voice on|off|toggle|status");
      return true;
    }
    const user = game.user;
    const actor = resolveActorForUser(user, ChatMessage.getSpeaker());
    const payload = {
      action,
      user_id: user?.id ?? null,
      user_name: user?.name ?? null,
      actor_id: actor?.id ?? null,
      actor_name: actor?.name ?? null,
      scene_id: canvas?.scene?.id ?? null,
    };
    if (!actor && (action === "on" || action === "start" || action === "enable" || action === "toggle")) {
      ui.notifications?.warn(
        "/voice: no actor assigned. Use !iam <name> first or select your token."
      );
      return true;
    }
    try {
      sendEventToPython("voice_control", payload);
      ui.notifications?.info(`AI DM voice: ${action}`);
    } catch (err) {
      console.warn("AI DM Bridge: /voice failed to send", err);
      ui.notifications?.error(`/voice failed: ${err}`);
    }
    return true;
  }

  // !<text> or /act <text> — send to the AI.
  let body = null;
  const bang = BANG_RE.exec(trimmed);
  if (bang) body = bang[1] || "";
  else {
    const slash = SLASH_RE.exec(trimmed);
    if (slash) body = slash[1] || "";
  }
  if (body === null) return false;

  console.log("AI DM Bridge: act intercepted:", trimmed);
  if (!_claim()) return true;
  performAct(body);
  return true;
}

Hooks.on("chatMessage", (chatLog, message, chatData) => {
  return _routeChatInput(message) ? false : true;
});

// --------------------------------------------------------------------- //
// preCreateChatMessage — universal V12-V15 catch-all. Runs just
// before any ChatMessage document is saved.
// --------------------------------------------------------------------- //

Hooks.on("preCreateChatMessage", (document, data, options, userId) => {
  // Only act on messages from this client, otherwise we'd intercept
  // every player's chat for every other player.
  if (userId && userId !== game.user.id) return true;

  const consumed = _routeChatInput(data?.content ?? document?.content ?? "");
  return consumed ? false : true;
});

// --------------------------------------------------------------------- //
// V14+ slash-command registration. We provide a real handler so
// Foundry doesn't fall through to a default empty IC message.
// --------------------------------------------------------------------- //

function registerSlashCommandV14() {
  const cls = foundry?.applications?.sidebar?.tabs?.ChatLog || globalThis.ChatLog;
  if (!cls) return false;
  // Use hasOwnProperty to avoid tripping V14's deprecated MESSAGE_PATTERNS getter.
  if (!Object.prototype.hasOwnProperty.call(cls, "CHAT_COMMANDS")) return false;
  const reg = cls.CHAT_COMMANDS;
  if (!reg) return false;

  const definition = {
    name: "act",
    command: "act",
    description: "Send an in-character action to the AI DM",
    pattern: /^\/act\b ?([\s\S]*)/i,
    icon: "fa-solid fa-comment",
    // Different V14 builds pass different signatures. Probe gently.
    handler: (...args) => {
      // Try to extract the body from any of the common shapes:
      //   (chatLog, match, chatData)
      //   (match, chatData)
      //   ({match, chatData})
      let body = "";
      for (const a of args) {
        if (!a) continue;
        if (Array.isArray(a) && a[1] !== undefined) { body = a[1]; break; }
        if (typeof a === "string" && a.startsWith("/act")) {
          body = a.replace(/^\/act\b ?/i, "");
          break;
        }
        if (typeof a === "object") {
          if (Array.isArray(a.match) && a.match[1] !== undefined) { body = a.match[1]; break; }
          if (typeof a.message === "string" && a.message.startsWith("/act")) {
            body = a.message.replace(/^\/act\b ?/i, "");
            break;
          }
        }
      }
      console.log("AI DM Bridge: /act invoked via CHAT_COMMANDS handler, body=", body);
      performAct(body);
      // Returning false in V14 prevents the default chat-message creation.
      return false;
    },
  };

  try {
    if (typeof reg.register === "function") {
      reg.register("act", definition);
    } else if (typeof reg.set === "function") {
      reg.set("act", definition);
    } else {
      reg.act = definition;
    }
    console.log("AI DM Bridge: registered /act with ChatLog.CHAT_COMMANDS");
    return true;
  } catch (err) {
    console.warn("AI DM Bridge: CHAT_COMMANDS registration failed", err);
    return false;
  }
}

Hooks.once("init", () => {
  console.log("AI DM Bridge: act chat handler installed (use ! <text> or /act <text>)");
  // Per-user "my actor" setting. Choices are populated lazily on
  // ``ready`` (when game.actors exists). Stored as the actor id.
  try {
    game.settings.register(MODULE_ID, SETTING_MY_ACTOR, {
      name: "AI DM: My Actor",
      hint: "The actor you control when you type ! or /act in chat. " +
            "Defaults to your assigned character. Override per-session " +
            "with the !iam <name|id> chat command.",
      scope: "client",
      config: true,
      type: String,
      default: "",
      // V13+ accepts choices as a function; older versions take a flat
      // map. We provide a flat map at ready-time via a SettingsConfig
      // re-render hook below so existing builds work.
      choices: { "": "(use default character)" },
      onChange: (val) => console.log("AI DM Bridge: my actor set to", val || "(default)"),
    });
  } catch (err) {
    console.warn("AI DM Bridge: failed to register settings", err);
  }
  registerSlashCommandV14();
});

// Populate the actor dropdown once world data is loaded. Foundry's
// SettingsConfig re-reads `choices` each time it renders, so we just
// keep the registered setting's choices map in sync.
Hooks.once("ready", () => {
  try {
    const setting = game.settings.settings.get(`${MODULE_ID}.${SETTING_MY_ACTOR}`);
    if (setting) {
      const choices = { "": "(use default character)" };
      for (const a of (game.actors ?? [])) {
        choices[a.id] = `${a.name} — ${a.type}`;
      }
      setting.choices = choices;
    }
  } catch (err) {
    console.warn("AI DM Bridge: failed to populate actor choices", err);
  }
});

// Expose programmatic entry for macros / debugging.
Hooks.once("ready", () => {
  globalThis.AIDMBridge = globalThis.AIDMBridge || {};
  globalThis.AIDMBridge.act = (text) => performAct(text);
  globalThis.AIDMBridge.sendEvent = (name, payload) => sendEventToPython(name, payload);
  globalThis.AIDMBridge.setMyActor = (idOrName) => {
    let target = game.actors?.get(idOrName);
    if (!target) target = game.actors?.find((a) => a.name === idOrName);
    if (!target) return null;
    _setMyPinnedActor(target.id);
    return target;
  };
});

