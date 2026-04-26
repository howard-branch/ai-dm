export async function activateScene(sceneId) {
  // Accept either Foundry's internal scene id or a human-readable name.
  // This lets campaign manifests use stable slugs (e.g. "candlekeep_courtyard")
  // instead of opaque auto-generated ids that change per-world.
  let scene = game.scenes?.get(sceneId);
  if (!scene) {
    scene = game.scenes?.find((s) => s.name === sceneId);
  }
  if (!scene) {
    throw new Error(`Scene not found: ${sceneId}`);
  }

  await scene.activate();
  return scene;
}

export async function createScene(name, width = 4000, height = 3000, grid = 100) {
  const existing = game.scenes?.find((s) => s.name === name);
  if (existing) {
    // Resize to match the requested dimensions if they differ. The
    // backend recomputes width/height from the campaign-pack anchors
    // each startup, so a freshly-authored scene that needs to shrink
    // (or grow) does the right thing on next launch instead of
    // staying stuck at last session's size.
    const patch = {};
    if (Number.isFinite(width) && width !== existing.width) patch.width = width;
    if (Number.isFinite(height) && height !== existing.height) patch.height = height;
    const curGrid = existing.grid?.size ?? existing.grid;
    if (Number.isFinite(grid) && grid !== curGrid) {
      // Foundry V11+ stores grid as an object {type, size, ...}.
      patch.grid = { ...(existing.grid?.toObject?.() ?? {}), size: grid };
    }
    if (Object.keys(patch).length) {
      try {
        await existing.update(patch);
        console.log(`AI DM Bridge: resized scene ${name} →`, patch);
      } catch (err) {
        console.warn("AI DM Bridge: scene resize failed", err);
      }
    }
    return existing;
  }

  const scene = await Scene.create({
    name,
    width,
    height,
    grid,
    padding: 0.25,
    tokenVision: true,
    navigation: true,
  });

  if (!scene) {
    throw new Error(`Failed to create scene: ${name}`);
  }

  return scene;
}

export async function deleteScene(sceneId) {
  const scene = game.scenes?.get(sceneId);
  if (!scene) {
    return { deleted: false, reason: "scene_not_found" };
  }
  await scene.delete();
  return { deleted: true };
}

export async function readActiveScene() {
  const scene = game.scenes?.active ?? game.scenes?.current;
  if (!scene) {
    return null;
  }
  return {
    id: scene.id,
    name: scene.name,
    width: scene.width,
    height: scene.height,
    grid: scene.grid?.size ?? scene.grid,
    tokens: (scene.tokens ?? []).map((t) => ({
      id: t.id,
      name: t.name,
      actorId: t.actorId,
      actorLink: !!t.actorLink,
      x: t.x,
      y: t.y,
    })),
    notes: (scene.notes ?? []).map((n) => ({
      id: n.id,
      text: n.text || n.label || "",
      x: n.x,
      y: n.y,
    })),
  };
}

/**
 * Drop a Note pin on the scene at (x, y) labelled ``text``. Idempotent
 * by case-insensitive label match — restarting the app does NOT create
 * duplicate pins.
 *
 * Anchors / interactables defined in the campaign pack are projected
 * here at startup so ``move_actor_to "valley overlook"`` and similar
 * verbal targets actually resolve via Foundry's ``findTargetOnScene``
 * (which searches notes by name).
 *
 * Foundry V11+ Notes can carry their own ``text`` override without
 * needing a backing JournalEntry, but some systems' canvas layer
 * refuses to render a Note with no JournalEntry id. To be safe we
 * auto-create a hidden parent JournalEntry the first time we're
 * called and reuse it forever.
 */
let _aiDmAnchorJournalId = null;

async function _getOrCreateAnchorJournal() {
  if (_aiDmAnchorJournalId) {
    const existing = game.journal?.get?.(_aiDmAnchorJournalId);
    if (existing) return existing;
    _aiDmAnchorJournalId = null;
  }
  const NAME = "AI DM Anchors";
  const found = game.journal?.find?.((j) => j.name === NAME);
  if (found) {
    _aiDmAnchorJournalId = found.id;
    return found;
  }
  const created = await JournalEntry.create({
    name: NAME,
    ownership: { default: CONST.DOCUMENT_OWNERSHIP_LEVELS.OBSERVER },
  });
  if (created) _aiDmAnchorJournalId = created.id;
  return created;
}

export async function createNote(sceneIdOrName, { x, y, text, icon = null } = {}) {
  if (!text) throw new Error("createNote: text is required");
  let scene = game.scenes?.get(sceneIdOrName);
  if (!scene) {
    const target = String(sceneIdOrName ?? "").toLowerCase();
    scene = game.scenes?.find((s) => (s.name || "").toLowerCase() === target);
  }
  if (!scene) scene = game.scenes?.active ?? game.scenes?.current;
  if (!scene) throw new Error(`Scene not found: ${sceneIdOrName}`);

  const wantedLower = String(text).toLowerCase();
  const existing = (scene.notes ?? []).find?.(
    (n) => (n.text || n.label || "").toLowerCase() === wantedLower
  );
  if (existing) {
    return { ok: true, noteId: existing.id, sceneId: scene.id, reused: true };
  }

  const journal = await _getOrCreateAnchorJournal();
  const data = {
    x: Number(x) || 0,
    y: Number(y) || 0,
    text,
    fontSize: 28,
    iconSize: 40,
    entryId: journal?.id ?? null,
  };
  if (icon) data.icon = icon;

  const created = await scene.createEmbeddedDocuments("Note", [data]);
  if (!created?.length) {
    throw new Error(`Failed to create note: ${text}`);
  }
  return {
    ok: true,
    noteId: created[0].id,
    sceneId: scene.id,
    reused: false,
  };
}
