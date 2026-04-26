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

// Convert dotted-path keys ("system.attributes.hp.max": 12) into a nested
// object suitable for Actor.create. Plain (non-dotted) keys are preserved
// as-is so callers can also pass an already-nested system patch.
function expandDotted(patch) {
  const out = {};
  if (!patch || typeof patch !== "object") return out;
  for (const [key, value] of Object.entries(patch)) {
    if (!key.includes(".")) {
      // Merge top-level objects rather than overwrite (so a caller may pass
      // both `system` (object) and dotted `system.x.y` keys safely).
      if (
        out[key] && typeof out[key] === "object" && !Array.isArray(out[key])
        && value && typeof value === "object" && !Array.isArray(value)
      ) {
        out[key] = foundry.utils.mergeObject(out[key], value, { inplace: false });
      } else {
        out[key] = value;
      }
      continue;
    }
    const parts = key.split(".");
    let cur = out;
    for (let i = 0; i < parts.length - 1; i++) {
      const p = parts[i];
      if (!cur[p] || typeof cur[p] !== "object") cur[p] = {};
      cur = cur[p];
    }
    cur[parts[parts.length - 1]] = value;
  }
  return out;
}

export async function createActor(name, actorType = "npc", options = {}) {
  const { system = null, img = null, items = [] } = options || {};
  const existing = game.actors?.find(
    (a) => (a.name || "").toLowerCase() === String(name).toLowerCase()
        && a.type === actorType
  );
  if (existing) {
    // Re-sync: push any provided system/img data onto the existing actor so
    // that wizard-built sheets stay in sync with Foundry across restarts.
    const updatePatch = {};
    if (system && typeof system === "object" && Object.keys(system).length > 0) {
      Object.assign(updatePatch, expandDotted(system));
    }
    if (img) updatePatch.img = img;
    if (Object.keys(updatePatch).length > 0) {
      try {
        await existing.update(updatePatch);
      } catch (err) {
        console.warn("AI DM Bridge: failed to sync existing actor", name, err);
      }
    }
    if (Array.isArray(items) && items.length > 0) {
      await syncEmbeddedItems(existing, items);
    }
    return existing;
  }

  const createData = {
    name,
    type: actorType,
  };
  if (img) createData.img = img;
  if (system && typeof system === "object" && Object.keys(system).length > 0) {
    Object.assign(createData, expandDotted(system));
  }

  const actor = await Actor.create(createData);

  if (!actor) {
    throw new Error(`Failed to create actor: ${name}`);
  }

  if (Array.isArray(items) && items.length > 0) {
    try {
      await actor.createEmbeddedDocuments("Item", items);
    } catch (err) {
      console.warn("AI DM Bridge: createEmbeddedDocuments failed for", name, err);
    }
  }

  return actor;
}

/**
 * Idempotently push embedded Item docs onto an existing actor: skip
 * any Item whose name + type already exists. Lets re-runs of bootstrap
 * stay quiet instead of duplicating the inventory each restart.
 */
async function syncEmbeddedItems(actor, items) {
  const existingKeys = new Set(
    (actor.items?.contents || []).map(
      (it) => `${(it.name || "").toLowerCase()}::${it.type}`
    )
  );
  const fresh = items.filter((it) => {
    const key = `${(it.name || "").toLowerCase()}::${it.type}`;
    return !existingKeys.has(key);
  });
  if (fresh.length === 0) return;
  try {
    await actor.createEmbeddedDocuments("Item", fresh);
  } catch (err) {
    console.warn("AI DM Bridge: syncEmbeddedItems failed for", actor?.name, err);
  }
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
