/**
 * Phase 1: chat-driven /act command.
 *
 * Listens for chat messages starting with "/act ", resolves the
 * sender → owned actor, and ships a {user_id, actor_id, text}
 * envelope to Python via the relay.
 */
import { sendPlayerInput } from "./socket_bridge.js";

const ACT_RE = /^\/act\s+(.+)$/is;

function resolveActorForUser(user, speaker) {
  // 1. Use the speaker the message was created with (token-controlled).
  if (speaker?.actor) {
    const a = game.actors?.get(speaker.actor);
    if (a) return a;
  }
  // 2. Fall back to the user's assigned default character.
  if (user?.character) return user.character;
  // 3. Last resort: first actor the user owns.
  const owned = game.actors?.filter((a) => a.testUserPermission(user, "OWNER")) || [];
  return owned[0] || null;
}

Hooks.on("chatMessage", (chatLog, message, chatData) => {
  const m = ACT_RE.exec(message || "");
  if (!m) return true;
  const text = m[1].trim();
  if (!text) {
    ui.notifications?.warn("/act requires some text — what does your character do?");
    return false;
  }

  const user = game.user;
  const speaker = chatData?.speaker || ChatMessage.getSpeaker();
  const actor = resolveActorForUser(user, speaker);

  if (!actor) {
    ui.notifications?.warn("/act: no actor assigned to your user. Drag a PC onto your user, or select a token you own.");
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

  try {
    sendPlayerInput(payload);
  } catch (err) {
    console.warn("AI DM Bridge: /act failed to send", err);
    ui.notifications?.error(`/act failed: ${err}`);
    return false;
  }

  // Echo the player's action into chat so the table can see it,
  // then suppress the raw "/act ..." line from being posted verbatim.
  ChatMessage.create({
    speaker: { alias: actor.name },
    content: `<em>${text}</em>`,
  });

  return false; // suppress default /act passthrough
});

