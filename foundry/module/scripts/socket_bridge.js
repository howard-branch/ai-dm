import { moveToken, moveActorTo, spawnToken, deleteToken, readToken } from "./token_commands.js";
import { activateScene, createScene, deleteScene, readActiveScene, createNote } from "./scene_commands.js";
import { createActor, updateActor, highlightObject, deleteActor, readActor } from "./actor_commands.js";

console.log("AI DM Bridge file loaded - top of module");

let relaySocket = null;
let reconnectTimer = null;

// Buffered outbound events while the relay is (re)connecting.
const pendingEvents = [];
// Bounded LRU of inbound event_ids so a relay replay does not render
// the same narration twice.
const seenEventIds = new Map();
const SEEN_EVENT_LIMIT = 256;

function rememberEventId(id) {
  if (!id) return;
  if (seenEventIds.has(id)) return;
  seenEventIds.set(id, true);
  while (seenEventIds.size > SEEN_EVENT_LIMIT) {
    const oldest = seenEventIds.keys().next().value;
    seenEventIds.delete(oldest);
  }
}

/**
 * Send an out-of-band {type:"event"} envelope to Python via the relay.
 * Used by chat_commands.js (`/act`), dm_panel.js, and combat_macros.js.
 */
export function sendEventToPython(eventName, payload) {
  const envelope = {
    type: "event",
    event: eventName,
    payload: payload || {},
    event_id: `evt-${foundry.utils.randomID(16)}`,
  };
  if (!relaySocket || relaySocket.readyState !== WebSocket.OPEN) {
    pendingEvents.push(envelope);
    console.warn("AI DM Bridge: relay not open, buffering event", eventName);
    return envelope.event_id;
  }
  relaySocket.send(JSON.stringify(envelope));
  return envelope.event_id;
}

export function sendPlayerInput(payload) {
  return sendEventToPython("player_input", payload);
}

export function sendStructuredIntent(payload) {
  return sendEventToPython("player_intent", payload);
}

function flushPendingEvents() {
  while (pendingEvents.length && relaySocket && relaySocket.readyState === WebSocket.OPEN) {
    const env = pendingEvents.shift();
    relaySocket.send(JSON.stringify(env));
  }
}

// Inbound event dispatch (Python → Foundry).
const inboundEventHandlers = new Map();

export function registerInboundEvent(name, handler) {
  inboundEventHandlers.set(name, handler);
}

/**
 * Election: returns true if THIS client is the chosen one to actually
 * mutate world state for an event broadcast to every Foundry tab.
 *
 * Rule: prefer the active GM. If no GM is connected, the player with
 * the lexicographically smallest user id wins. Every other client
 * skips the side-effect (e.g. ChatMessage.create) — but Foundry's own
 * server then replicates the chosen client's ChatMessage to every
 * connected tab, so all players still SEE the narration in their
 * chat sidebar.
 *
 * This avoids the two failure modes of a static rule:
 *  - "GM-only" → pure-player sessions see nothing at all.
 *  - "all clients" → N duplicate ChatMessages per event.
 */
function isElectedRenderer() {
  const me = game?.user;
  if (!me) return false;
  const active = (game.users?.contents ?? game.users ?? [])
      .filter((u) => u?.active);
  if (!active.length) return true; // we're alone; render.
  const gms = active.filter((u) => u.isGM);
  if (gms.length) {
    // Lowest-id GM wins (deterministic tiebreak).
    gms.sort((a, b) => String(a.id).localeCompare(String(b.id)));
    return me.id === gms[0].id;
  }
  // No GM connected — lowest-id active user wins.
  active.sort((a, b) => String(a.id).localeCompare(String(b.id)));
  return me.id === active[0].id;
}

// Bounded LRU of recently-processed request ids so that a relay replay does
// not run the same command twice. Cached responses are re-emitted on replay.
const PROCESSED_LRU_LIMIT = 512;
const processedResults = new Map(); // request_id -> last result envelope

function rememberProcessed(requestId, envelope) {
  if (processedResults.has(requestId)) {
    processedResults.delete(requestId);
  }
  processedResults.set(requestId, envelope);
  while (processedResults.size > PROCESSED_LRU_LIMIT) {
    const oldest = processedResults.keys().next().value;
    processedResults.delete(oldest);
  }
}

async function handleCommand(command) {
  if (!command || !command.type) {
    console.warn("AI DM Bridge: invalid command", command);
    return { ok: false, error: "invalid_command", command_type: command?.type ?? null };
  }

  const _t0 = performance.now();
  try {
    const result = await _dispatchCommand(command);
    const ms = (performance.now() - _t0).toFixed(0);
    console.log(`AI DM Bridge: ${command.type} ${result?.ok ? "ok" : "FAIL"} in ${ms}ms`);
    return result;
  } catch (err) {
    const ms = (performance.now() - _t0).toFixed(0);
    console.error(`AI DM Bridge: ${command.type} threw after ${ms}ms`, command, err);
    return { ok: false, error: String(err), command_type: command.type };
  }
}

async function _dispatchCommand(command) {
  try {
    switch (command.type) {
      case "move_token":
        await moveToken(command.token_id, command.x, command.y);
        return { ok: true, command_type: command.type };

      case "move_actor_to": {
        const result = await moveActorTo({
          actor_id: command.actor_id,
          target: command.target ?? null,
          target_token_id: command.target_id ?? null,
          scene_id: command.scene_id ?? null,
          x: command.x ?? null,
          y: command.y ?? null,
          distance_ft: command.distance_ft ?? null,
          direction: command.direction ?? null,
          formation_index: command.formation_index ?? null,
          formation_count: command.formation_count ?? null,
        });
        return { ok: true, command_type: command.type, ...result };
      }

      case "update_actor":
        await updateActor(command.actor_id, command.patch || {});
        return { ok: true, command_type: command.type };

      case "apply_damage": {
        // Python is authoritative on the new HP. We translate amount
        // → an actor.update({system.attributes.hp.value}) so the
        // sheet/token bar matches. Best-effort actor lookup: id, then
        // name.
        const aid = command.actor_id;
        const amount = Number(command.amount || 0);
        let actor = aid ? game.actors?.get(aid) : null;
        if (!actor && aid) {
          actor = (game.actors?.contents ?? []).find(
            (a) => a.name === aid || a.name?.toLowerCase() === String(aid).toLowerCase()
          ) || null;
        }
        if (!actor) {
          console.warn("AI DM Bridge: apply_damage — no such actor", aid);
          return { ok: false, error: "unknown_actor", command_type: command.type };
        }
        const hpPath = "system.attributes.hp";
        const hp = foundry.utils.getProperty(actor, hpPath) || {};
        const cur = Number(hp.value ?? 0);
        const tmp = Number(hp.temp ?? 0);
        const absorbed = Math.min(tmp, amount);
        const remaining = amount - absorbed;
        const newTmp = Math.max(0, tmp - absorbed);
        const newHp = Math.max(0, cur - remaining);
        try {
          await actor.update({
            "system.attributes.hp.value": newHp,
            "system.attributes.hp.temp": newTmp,
          });
        } catch (err) {
          console.warn("AI DM Bridge: apply_damage actor.update failed", err);
          return { ok: false, error: String(err), command_type: command.type };
        }
        return {
          ok: true,
          command_type: command.type,
          actorId: actor.id,
          hpBefore: cur,
          hpAfter: newHp,
          tempAbsorbed: absorbed,
          dealt: remaining,
          damageType: command.damage_type ?? "untyped",
          crit: !!command.crit,
        };
      }

      case "highlight_object":
        await highlightObject(command.target_id);
        return { ok: true, command_type: command.type };

      case "create_scene": {
        const scene = await createScene(
            command.name,
            command.width,
            command.height,
            command.grid
        );
        return { ok: true, command_type: command.type, sceneId: scene.id, sceneName: scene.name };
      }

      case "activate_scene":
        await activateScene(command.scene_id);
        return { ok: true, command_type: command.type };

      case "create_note": {
        const result = await createNote(command.scene_id, {
          x: command.x,
          y: command.y,
          text: command.text,
          icon: command.icon ?? null,
        });
        return { ok: true, command_type: command.type, ...result };
      }

      case "spawn_token": {
        const token = await spawnToken(
            command.scene_id,
            command.actor_id,
            command.x,
            command.y,
            command.name
        );
        return { ok: true, command_type: command.type, tokenId: token.id, sceneId: command.scene_id };
      }

      case "create_actor": {
        const actor = await createActor(
            command.name,
            command.actor_type || "npc",
            { system: command.system || null, img: command.img || null, items: command.items || [] }
        );
        return { ok: true, command_type: command.type, actorId: actor.id, actorName: actor.name };
      }

      case "delete_token":
        await deleteToken(command.scene_id, command.token_id);
        return { ok: true, command_type: command.type };

      case "delete_actor":
        await deleteActor(command.actor_id);
        return { ok: true, command_type: command.type };

      case "delete_scene":
        await deleteScene(command.scene_id);
        return { ok: true, command_type: command.type };

      case "read_token": {
        const snapshot = await readToken(command.scene_id, command.token_id);
        return { ok: true, command_type: command.type, snapshot };
      }

      case "read_actor": {
        const snapshot = await readActor(command.actor_id);
        return { ok: true, command_type: command.type, snapshot };
      }

      case "read_active_scene": {
        const snapshot = await readActiveScene();
        return { ok: true, command_type: command.type, snapshot };
      }

      default:
        console.warn("AI DM Bridge: unsupported command", command.type);
        return { ok: false, error: "unsupported_command", command_type: command.type };
    }
  } catch (err) {
    console.error("AI DM Bridge command failed", command, err);
    return { ok: false, error: String(err), command_type: command.type };
  }
}

async function handleBatch(commands) {
  const results = [];
  for (const command of commands || []) {
    results.push(await handleCommand(command));
  }
  return results;
}

function buildResultEnvelope(requestId, payload, isBatch) {
  const envelope = {
    type: "result",
    request_id: requestId,
    result: payload,
  };
  if (!isBatch && payload && typeof payload === "object" && payload.command_type) {
    envelope.command_type = payload.command_type;
    envelope.ok = !!payload.ok;
  } else if (isBatch) {
    envelope.command_type = "batch";
    envelope.ok = Array.isArray(payload) && payload.every((r) => r && r.ok);
  }
  return envelope;
}

function connectRelay() {
  const url = "ws://127.0.0.1:8765";
  console.log(`AI DM Bridge connecting to relay: ${url}`);

  relaySocket = new WebSocket(url);

  relaySocket.addEventListener("open", () => {
    console.log("AI DM Bridge relay connected");
    const hello = {
      type: "hello",
      client: "foundry",
      user_id: game?.user?.id ?? null,
      user_name: game?.user?.name ?? null,
      is_gm: !!game?.user?.isGM,
    };
    relaySocket.send(JSON.stringify(hello));
    console.log("AI DM Bridge hello sent:", hello);
    flushPendingEvents();
  });

  relaySocket.addEventListener("message", async (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (err) {
      console.warn("AI DM Bridge invalid relay JSON", event.data);
      return;
    }

    if (msg.type === "hello_ack") {
      console.log("AI DM Bridge relay handshake complete");
      return;
    }

    if (msg.type === "command" || msg.type === "batch") {
      if (!msg.request_id || typeof msg.request_id !== "string") {
        console.warn("AI DM Bridge: missing request_id, dropping", msg);
        return;
      }

      // Only the GM client executes mutating commands. Foundry's relay
      // broadcasts to every connected client; without this gate the
      // Player browser also tries to run create_scene / activate_scene
      // / create_actor and gets "lacks permission" errors. The GM
      // client owns the world authority — it speaks for everyone.
      if (!game?.user?.isGM) {
        console.log(
          `AI DM Bridge: ignoring ${msg.type} on non-GM client ` +
          `(user=${game?.user?.name}, isGM=${!!game?.user?.isGM})`,
          msg.request_id
        );
        return;
      }
      console.log(
        `AI DM Bridge: executing ${msg.type} on GM client ` +
        `(user=${game?.user?.name})`,
        msg.request_id
      );

      // Replay protection: re-emit cached result instead of re-executing.
      const cached = processedResults.get(msg.request_id);
      if (cached) {
        console.warn("AI DM Bridge: duplicate request, re-emitting cached result", msg.request_id);
        relaySocket.send(JSON.stringify(cached));
        return;
      }

      const isBatch = msg.type === "batch";
      const payload = isBatch
          ? await handleBatch(msg.commands || [])
          : await handleCommand(msg.command);

      const envelope = buildResultEnvelope(msg.request_id, payload, isBatch);
      rememberProcessed(msg.request_id, envelope);
      relaySocket.send(JSON.stringify(envelope));
      return;
    }

    if (msg.type === "pong") {
      return;
    }

    if (msg.type === "event") {
      const eventId = msg.event_id;
      if (eventId && seenEventIds.has(eventId)) {
        return;
      }
      rememberEventId(eventId);
      const handler = inboundEventHandlers.get(msg.event);
      if (handler) {
        try {
          await handler(msg.payload || {});
        } catch (err) {
          console.warn("AI DM Bridge inbound event handler failed", msg.event, err);
        }
      } else {
        console.warn("AI DM Bridge: no handler for inbound event", msg.event);
      }
      return;
    }

    console.warn("AI DM Bridge unhandled relay message", msg);
  });

  relaySocket.addEventListener("close", () => {
    console.warn("AI DM Bridge relay disconnected");
    relaySocket = null;
    scheduleReconnect();
  });

  relaySocket.addEventListener("error", (err) => {
    console.warn("AI DM Bridge relay error", err);
  });
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectRelay();
  }, 3000);
}

// Placeholder: future home of inbound Foundry → Python hooks (e.g. forward
// updateToken / updateActor / canvasReady events back across the relay so
// the Python side can react to GM-driven changes). Defined as a no-op so
// the module loads cleanly until the inbound channel is implemented.
function installInboundHooks() {
  // intentionally empty
}

Hooks.once("ready", async () => {
  console.log("AI DM Bridge ready");
  console.log("AI DM Bridge assigning global");
  globalThis.AIDMBridge = {
    async handleCommand(command) {
      return handleCommand(command);
    },
    async handleBatch(commands) {
      return handleBatch(commands);
    },
    sendPlayerInput,
    sendStructuredIntent,
    registerInboundEvent,
  };
  console.log("AI DM Bridge assigning global - done");

  // Default inbound: render a narration envelope.
  // Every Foundry client appends to its own persistent narration-log
  // window (so each player has a full transcript even with the chat
  // sidebar collapsed). The elected client (active GM, or lowest-id
  // player otherwise) additionally calls ``ChatMessage.create()``;
  // Foundry's own server then replicates that ChatMessage to every
  // tab, so the chat sidebar stays in sync with no duplicates.
  registerInboundEvent("narration", async (payload) => {
    const { renderNarration } = await import("./narration_renderer.js");
    return renderNarration(payload, { createChatMessage: isElectedRenderer() });
  });

  // Wipe the Foundry chat log + the per-client persistent narration
  // log. Triggered by ``scripts/reset_state.sh`` so a fresh scenario
  // run doesn't carry stale dialogue from the previous session.
  registerInboundEvent("clear_chat", async () => {
    // Every client clears its own local narration log.
    try {
      const { NarrationLog } = await import("./narration_log.js");
      NarrationLog.clear();
    } catch (err) { /* ignore */ }
    // Only the elected (GM) client deletes the world chat — Foundry
    // replicates the deletions to every tab.
    if (!isElectedRenderer()) return;
    try {
      const ids = (game.messages?.contents ?? []).map((m) => m.id);
      if (ids.length) {
        await ChatMessage.deleteDocuments(ids);
      }
      console.log(`AI DM Bridge: cleared ${ids.length} chat message(s)`);
    } catch (err) {
      console.warn("AI DM Bridge: clear_chat failed", err);
    }
  });

  // Reset Foundry world state (scenes / actors / journals / tokens
  // created by the AI DM). Triggered by ``scripts/reset_state.sh`` so
  // a fresh scenario run starts with a clean Foundry world. The Python
  // side computes the names to delete from the campaign pack and
  // sends them in the payload — we don't guess on the JS side.
  //
  // Payload shape:
  //   {
  //     scene_names:    [str, ...],   // delete by exact name match
  //     actor_names:    [str, ...],
  //     journal_names:  [str, ...],   // also wipes notes that pointed at them
  //     delete_all_tokens_in_listed_scenes: bool   // default true
  //   }
  registerInboundEvent("reset_foundry_state", async (payload) => {
    if (!isElectedRenderer()) return;
    if (!game?.user?.isGM) {
      console.warn("AI DM Bridge: reset_foundry_state ignored (not GM)");
      return;
    }
    const sceneNames = new Set((payload?.scene_names || []).map((s) => String(s).toLowerCase()));
    const actorNames = new Set((payload?.actor_names || []).map((s) => String(s).toLowerCase()));
    const journalNames = new Set((payload?.journal_names || []).map((s) => String(s).toLowerCase()));
    const wipeTokens = payload?.delete_all_tokens_in_listed_scenes !== false;
    const summary = { scenes: 0, actors: 0, journals: 0, tokens: 0, notes: 0, errors: [] };

    // 1. Tokens + notes inside scenes we are about to delete (so the
    //    scene delete itself is uncluttered) AND in any matched scene.
    try {
      for (const scene of (game.scenes?.contents ?? [])) {
        const matches =
          sceneNames.has((scene.name || "").toLowerCase()) ||
          sceneNames.has((scene.id || "").toLowerCase());
        if (!matches) continue;
        if (wipeTokens) {
          const tokenIds = (scene.tokens?.contents ?? []).map((t) => t.id);
          if (tokenIds.length) {
            try {
              await scene.deleteEmbeddedDocuments("Token", tokenIds);
              summary.tokens += tokenIds.length;
            } catch (e) { summary.errors.push(`tokens@${scene.name}: ${e.message}`); }
          }
        }
        const noteIds = (scene.notes?.contents ?? []).map((n) => n.id);
        if (noteIds.length) {
          try {
            await scene.deleteEmbeddedDocuments("Note", noteIds);
            summary.notes += noteIds.length;
          } catch (e) { summary.errors.push(`notes@${scene.name}: ${e.message}`); }
        }
      }
    } catch (e) { summary.errors.push(`tokens/notes: ${e.message}`); }

    // 2. Scenes by name.
    try {
      const sceneIds = (game.scenes?.contents ?? [])
        .filter((s) =>
          sceneNames.has((s.name || "").toLowerCase()) ||
          sceneNames.has((s.id || "").toLowerCase()))
        .map((s) => s.id);
      if (sceneIds.length) {
        await Scene.deleteDocuments(sceneIds);
        summary.scenes = sceneIds.length;
      }
    } catch (e) { summary.errors.push(`scenes: ${e.message}`); }

    // 3. Actors by name.
    try {
      const actorIds = (game.actors?.contents ?? [])
        .filter((a) =>
          actorNames.has((a.name || "").toLowerCase()) ||
          actorNames.has((a.id || "").toLowerCase()))
        .map((a) => a.id);
      if (actorIds.length) {
        await Actor.deleteDocuments(actorIds);
        summary.actors = actorIds.length;
      }
    } catch (e) { summary.errors.push(`actors: ${e.message}`); }

    // 4. Journal entries by name (always include the AI DM Anchors
    //    auto-journal even if Python forgot to list it).
    try {
      journalNames.add("ai dm anchors");
      const journalIds = (game.journal?.contents ?? [])
        .filter((j) => journalNames.has((j.name || "").toLowerCase()))
        .map((j) => j.id);
      if (journalIds.length) {
        await JournalEntry.deleteDocuments(journalIds);
        summary.journals = journalIds.length;
      }
    } catch (e) { summary.errors.push(`journals: ${e.message}`); }

    // 5. Clear the persistent log on every client too (so the new
    //    session also starts visually clean).
    try {
      const { NarrationLog } = await import("./narration_log.js");
      NarrationLog.clear();
    } catch { /* ignore */ }

    console.log("AI DM Bridge: reset_foundry_state →", summary);
    try {
      ui.notifications?.info(
        `AI DM reset: ${summary.scenes} scene(s), ${summary.actors} actor(s), ` +
        `${summary.journals} journal(s), ${summary.tokens} token(s), ${summary.notes} note(s).`
      );
    } catch { /* ignore */ }
  });

  // Lobby status: GM-only chat whisper showing who has joined and how
  // to start the game (`/startgame`).
  registerInboundEvent("lobby_status", async (payload) => {
    const { renderLobbyStatus } = await import("./lobby_renderer.js");
    return renderLobbyStatus(payload);
  });

  // Phase 4: interactive roll prompts.
  //   request_player_roll → render a chat card with a "Roll" button.
  //                          The click handler (installed below) fires
  //                          ``player_roll_resolved`` back to Python.
  //   chat_roll           → DM-side roll already resolved by Python;
  //                          render a styled chat card for visibility.
  {
    const { renderRollPrompt, renderChatRoll, installRollPromptHooks } =
        await import("./roll_prompt.js");
    registerInboundEvent("request_player_roll", async (payload) => renderRollPrompt(payload));
    registerInboundEvent("chat_roll", async (payload) => renderChatRoll(payload));
    installRollPromptHooks();
  }

  // Character-creation wizard prompt from Python. Opens a Dialog and
  // sends the player's choices back as a wizard_response event.
  registerInboundEvent("wizard_request", async (payload) => {
    const { openCharacterWizard } = await import("./character_wizard.js");
    return openCharacterWizard(payload, sendEventToPython);
  });

  // Bind a Foundry user to a freshly-created actor so the player isn't
  // prompted to "choose a character" on next login. Only the GM client
  // has permission to mutate user.character — non-GM clients no-op.
  registerInboundEvent("assign_player_character", async (payload) => {
    const userId = payload?.user_id;
    const actorId = payload?.actor_id;
    if (!userId || !actorId) {
      console.warn("AI DM Bridge: assign_player_character missing ids", payload);
      return;
    }
    if (!game?.user?.isGM) {
      // Wait for the GM client to do it.
      return;
    }
    const user = game.users?.get(userId);
    const actor = game.actors?.get(actorId);
    if (!user) {
      console.warn("AI DM Bridge: assign_player_character — no such user", userId);
      return;
    }
    if (!actor) {
      console.warn("AI DM Bridge: assign_player_character — no such actor", actorId);
      return;
    }
    try {
      await user.update({ character: actorId });
      // Make sure the player can actually own the character they just made.
      const perms = foundry.utils.deepClone(actor.ownership || {});
      perms[userId] = CONST.DOCUMENT_OWNERSHIP_LEVELS.OWNER;
      await actor.update({ ownership: perms });
      console.log(
        `AI DM Bridge: bound user ${user.name} → actor ${actor.name} (${actorId})`
      );
    } catch (err) {
      console.warn("AI DM Bridge: assign_player_character failed", err);
    }
  });

  installInboundHooks();
  connectRelay();
});