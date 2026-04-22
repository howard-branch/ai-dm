// Resolve a scene by Foundry id OR by name (case-insensitive), so
// campaign manifests can use stable human slugs instead of opaque
// auto-generated ids.
function resolveScene(sceneIdOrName) {
  if (!sceneIdOrName) return game.scenes?.current ?? null;
  const target = String(sceneIdOrName).toLowerCase();
  return (
    game.scenes?.get(sceneIdOrName) ??
    game.scenes?.find((s) => (s.name || "").toLowerCase() === target) ??
    null
  );
}

// Same idea for actors: id, then case-insensitive name match.
function resolveActor(actorIdOrName) {
  if (!actorIdOrName) return null;
  const target = String(actorIdOrName).toLowerCase();
  return (
    game.actors?.get(actorIdOrName) ??
    game.actors?.find((a) => (a.name || "").toLowerCase() === target) ??
    null
  );
}

export async function moveToken(tokenId, x, y) {
  const scene = game.scenes?.current;
  if (!scene) {
    throw new Error("No current scene available");
  }

  const existing =
      scene.getEmbeddedDocument?.("Token", tokenId) ??
      scene.tokens?.get?.(tokenId);

  if (!existing) {
    throw new Error(`Token not found in current scene ${scene.id}: ${tokenId}`);
  }

  const updated = await scene.updateEmbeddedDocuments("Token", [
    { _id: tokenId, x, y }
  ]);

  if (!updated?.length) {
    throw new Error(`Failed to move token: ${tokenId}`);
  }

  return updated[0];
}

export async function spawnToken(sceneId, actorId, x, y, name = null) {
  const scene = resolveScene(sceneId);
  if (!scene) {
    throw new Error(`Scene not found: ${sceneId}`);
  }

  const actor = resolveActor(actorId);
  if (!actor) {
    throw new Error(`Actor not found: ${actorId}`);
  }

  const tokenDoc = await actor.getTokenDocument({
    x,
    y,
    name: name ?? actor.name,
  });

  const created = await scene.createEmbeddedDocuments("Token", [
    tokenDoc.toObject()
  ]);

  if (!created?.length) {
    throw new Error(`Failed to create token for actor: ${actorId}`);
  }

  return created[0];
}

export async function deleteToken(sceneId, tokenId) {
  const scene = resolveScene(sceneId);
  if (!scene) {
    throw new Error(`Scene not found: ${sceneId ?? "<current>"}`);
  }

  const existing =
      scene.getEmbeddedDocument?.("Token", tokenId) ??
      scene.tokens?.get?.(tokenId);

  if (!existing) {
    return { deleted: false, reason: "token_not_found" };
  }

  await scene.deleteEmbeddedDocuments("Token", [tokenId]);
  return { deleted: true };
}

export async function readToken(sceneId, tokenId) {
  const scene = resolveScene(sceneId);
  if (!scene) {
    throw new Error(`Scene not found: ${sceneId ?? "<current>"}`);
  }

  const token =
      scene.getEmbeddedDocument?.("Token", tokenId) ??
      scene.tokens?.get?.(tokenId);

  if (!token) {
    return null;
  }

  return {
    id: token.id,
    sceneId: scene.id,
    name: token.name,
    actorId: token.actorId,
    x: token.x,
    y: token.y,
  };
}
