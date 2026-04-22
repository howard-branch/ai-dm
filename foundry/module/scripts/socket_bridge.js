import { moveToken, spawnToken, deleteToken, readToken } from "./token_commands.js";
import { activateScene, createScene, deleteScene, readActiveScene } from "./scene_commands.js";
import { createActor, updateActor, highlightObject, deleteActor, readActor } from "./actor_commands.js";

console.log("AI DM Bridge file loaded - top of module");

let relaySocket = null;
let reconnectTimer = null;

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

  try {
    switch (command.type) {
      case "move_token":
        await moveToken(command.token_id, command.x, command.y);
        return { ok: true, command_type: command.type };

      case "update_actor":
        await updateActor(command.actor_id, command.patch || {});
        return { ok: true, command_type: command.type };

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
            command.actor_type || "npc"
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
    relaySocket.send(JSON.stringify({
      type: "hello",
      client: "foundry",
    }));
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
  };
  console.log("AI DM Bridge assigning global - done");
  installInboundHooks();
  connectRelay();
});