// Resolve an actor by Foundry id OR by case-insensitive name. Lets
// campaign manifests reference actors by stable human slugs.
function resolveActor(actorIdOrName) {
  if (!actorIdOrName) return null;
  const target = String(actorIdOrName).toLowerCase();
  return (
    game.actors?.get(actorIdOrName) ??
    game.actors?.find((a) => (a.name || "").toLowerCase() === target) ??
    null
  );
}

export async function createActor(name, actorType = "npc") {
  const existing = game.actors?.find(
    (a) => (a.name || "").toLowerCase() === String(name).toLowerCase()
        && a.type === actorType
  );
  if (existing) {
    return existing;
  }

  const actor = await Actor.create({
    name,
    type: actorType,
  });

  if (!actor) {
    throw new Error(`Failed to create actor: ${name}`);
  }

  return actor;
}

export async function updateActor(actorId, patch) {
  const actor = resolveActor(actorId);
  if (!actor) {
    throw new Error(`Actor not found: ${actorId}`);
  }

  await actor.update(patch);
}

export async function highlightObject(targetId) {
  if (!canvas?.scene) {
    throw new Error("No active scene on canvas");
  }

  let token = canvas.tokens?.placeables?.find((t) => t.id === targetId);
  if (!token) {
    token = canvas.tokens?.placeables?.find((t) => t.name === targetId);
  }

  if (token) {
    token.control({ releaseOthers: true });
    await canvas.animatePan({ x: token.center.x, y: token.center.y, scale: 1.2 });
    return;
  }

  let note = canvas.notes?.placeables?.find((n) => n.id === targetId);
  if (!note) {
    note = canvas.notes?.placeables?.find((n) => n.document?.text === targetId);
  }

  if (note) {
    await canvas.animatePan({ x: note.center.x, y: note.center.y, scale: 1.2 });
    return;
  }

  throw new Error(`Nothing highlightable found for target_id: ${targetId}`);
}

export async function deleteActor(actorId) {
  const actor = resolveActor(actorId);
  if (!actor) {
    return { deleted: false, reason: "actor_not_found" };
  }
  await actor.delete();
  return { deleted: true };
}

export async function readActor(actorId) {
  const actor = resolveActor(actorId);
  if (!actor) {
    return null;
  }
  return {
    id: actor.id,
    name: actor.name,
    type: actor.type,
    img: actor.img,
    system: actor.system ?? null,
  };
}
