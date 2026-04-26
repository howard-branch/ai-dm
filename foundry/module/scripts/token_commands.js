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

  // {animate: false} → skip the slide animation. Foundry otherwise
  // awaits animation completion before resolving the promise, which
  // can exceed our queue timeout on long moves and produce the
  // "Timed out waiting for Foundry response to move_token" error.
  // The token still updates visibly — it just teleports.
  const updated = await scene.updateEmbeddedDocuments(
      "Token",
      [{ _id: tokenId, x, y }],
      { animate: false }
  );

  if (!updated?.length) {
    throw new Error(`Failed to move token: ${tokenId}`);
  }

  return updated[0];
}

/**
 * Find the token in `scene` that represents `actorIdOrName`.
 * Returns the Token document or null.
 */
function findActorToken(scene, actorIdOrName) {
  if (!scene || !actorIdOrName) return null;
  const actor = resolveActor(actorIdOrName);
  const wantedActorId = actor?.id ?? null;
  const target = String(actorIdOrName).toLowerCase();
  for (const tok of scene.tokens ?? []) {
    if (wantedActorId && tok.actorId === wantedActorId) return tok;
    if ((tok.name || "").toLowerCase() === target) return tok;
    if (tok.id === actorIdOrName) return tok;
  }
  return null;
}

/**
 * Find a target on the scene by name or id. Searches tokens (excluding
 * `excludeTokenId`) then notes. Returns {x, y, width, height} or null.
 */
function findTargetOnScene(scene, target, excludeTokenId = null) {
  if (!scene || !target) return null;
  const want = String(target).toLowerCase().trim();
  const wantWords = _wordSet(want);

  // Tokens by id
  const byId = scene.tokens?.get?.(target);
  if (byId && byId.id !== excludeTokenId) {
    return { x: byId.x, y: byId.y, width: byId.width, height: byId.height };
  }

  // Pass 1: exact / substring match on tokens.
  let bestToken = null;
  let bestTokenScore = 0;
  for (const tok of scene.tokens ?? []) {
    if (tok.id === excludeTokenId) continue;
    const n = (tok.name || "").toLowerCase();
    if (!n) continue;
    if (n === want) {
      return { x: tok.x, y: tok.y, width: tok.width, height: tok.height };
    }
    const s = _matchScore(n, want, wantWords);
    if (s > bestTokenScore) { bestTokenScore = s; bestToken = tok; }
  }

  // Pass 2: notes / journal pins.
  let bestNote = null;
  let bestNoteScore = 0;
  for (const note of scene.notes ?? []) {
    const n = (note.text || note.label || "").toLowerCase();
    if (!n) continue;
    if (n === want) {
      return { x: note.x, y: note.y, width: 1, height: 1 };
    }
    const s = _matchScore(n, want, wantWords);
    if (s > bestNoteScore) { bestNoteScore = s; bestNote = note; }
  }

  // Pass 3: drawings (text labels).
  let bestDrawing = null;
  let bestDrawingScore = 0;
  for (const drawing of scene.drawings ?? []) {
    const n = (drawing.text || "").toLowerCase();
    if (!n) continue;
    if (n === want) {
      return { x: drawing.x, y: drawing.y, width: 1, height: 1 };
    }
    const s = _matchScore(n, want, wantWords);
    if (s > bestDrawingScore) { bestDrawingScore = s; bestDrawing = drawing; }
  }

  // Prefer tokens > notes > drawings on equal score; require at least
  // one shared word OR substring containment to avoid wild guesses.
  const ranked = [
    [bestToken, bestTokenScore, "token"],
    [bestNote, bestNoteScore, "note"],
    [bestDrawing, bestDrawingScore, "drawing"],
  ].filter(([obj, s]) => obj && s > 0);
  if (!ranked.length) return null;
  ranked.sort((a, b) => b[1] - a[1]);
  const [obj, score, kind] = ranked[0];
  console.log(
    `AI DM Bridge: fuzzy-matched ${kind} ${JSON.stringify(obj.name ?? obj.text ?? obj.label)} ` +
    `for target ${JSON.stringify(target)} (score=${score.toFixed(2)})`
  );
  if (kind === "token") {
    return { x: obj.x, y: obj.y, width: obj.width, height: obj.height };
  }
  return { x: obj.x, y: obj.y, width: 1, height: 1 };
}

// Tokenise a label into a Set of meaningful words (drops stop-words
// and underscores so "pass_overlook" / "Valley Overlook" / "the
// overlook" all yield the comparable {"overlook"}-bearing sets).
const _STOP_WORDS = new Set([
  "the", "a", "an", "of", "to", "at", "on", "in", "and", "or",
  "with", "by", "for", "near", "from",
]);
function _wordSet(s) {
  const words = String(s ?? "")
      .toLowerCase()
      .split(/[^a-z0-9]+/)
      .filter((w) => w && !_STOP_WORDS.has(w));
  return new Set(words);
}

// 0.0–1.0 confidence that ``name`` matches ``want``.
//   1.0 → exact (handled by caller)
//   0.9 → substring containment either direction
//   0.0–0.85 → Jaccard overlap of word sets (shared / total)
function _matchScore(name, want, wantWords) {
  if (name.includes(want) || want.includes(name)) return 0.9;
  const nameWords = _wordSet(name);
  if (!nameWords.size || !wantWords.size) return 0;
  let shared = 0;
  for (const w of wantWords) if (nameWords.has(w)) shared++;
  if (!shared) return 0;
  const union = new Set([...nameWords, ...wantWords]).size;
  return 0.85 * (shared / union);
}

/**
 * Move the token controlled by `actorIdOrName` toward `target` (a token,
 * note, or coordinate) on `sceneIdOrName`. Resolves both the actor and
 * the target server-side so callers don't need to know Foundry's
 * internal token ids.
 *
 * Returns { ok, tokenId, actorId, fromX, fromY, x, y, target } or throws.
 */
export async function moveActorTo({
  actor_id,
  target = null,
  target_token_id = null,
  scene_id = null,
  x = null,
  y = null,
} = {}) {
  const scene = resolveScene(scene_id);
  if (!scene) throw new Error(`Scene not found: ${scene_id ?? "<current>"}`);

  const myToken = findActorToken(scene, actor_id);
  if (!myToken) {
    throw new Error(
      `move_actor_to: no token for actor ${actor_id} in scene ${scene.id}`
    );
  }

  let destX = null;
  let destY = null;
  let resolvedFrom = null;

  if (Number.isFinite(x) && Number.isFinite(y)) {
    destX = x;
    destY = y;
    resolvedFrom = "explicit_xy";
  } else {
    let targetInfo = null;
    if (target_token_id) {
      const tt = scene.tokens?.get?.(target_token_id);
      if (tt) {
        targetInfo = { x: tt.x, y: tt.y, width: tt.width, height: tt.height };
        resolvedFrom = `token:${tt.name || tt.id}`;
      }
    }
    if (!targetInfo && target) {
      targetInfo = findTargetOnScene(scene, target, myToken.id);
      if (targetInfo) resolvedFrom = `name:${target}`;
    }
    if (!targetInfo) {
      // Help the player diagnose why their "/act move to X" did
      // nothing: list the names the GM client could actually have
      // matched against. Otherwise the failure is silent except for
      // the bridge timing log.
      const tokenNames = (scene.tokens ?? [])
          .map((t) => t.name)
          .filter(Boolean);
      const noteNames = (scene.notes ?? [])
          .map((n) => n.text || n.label)
          .filter(Boolean);
      console.warn(
        `AI DM Bridge: move_actor_to could not resolve target ` +
        `${target ?? target_token_id ?? "(none)"} on scene ${scene.id}. ` +
        `Tokens: [${tokenNames.join(", ")}]. Notes: [${noteNames.join(", ")}].`
      );
      // Include the available names in the error itself so the Python
      // side can surface them back to the player in chat — otherwise
      // they only show up in the GM browser's devtools console.
      const available = [...tokenNames, ...noteNames]
          .filter(Boolean)
          .slice(0, 12);
      const availStr = available.length
          ? ` available: ${available.join(", ")}`
          : " no named tokens or notes on this scene";
      throw new Error(
        `move_actor_to: target not found on scene ${scene.id}: ` +
        `${target ?? target_token_id ?? "(none)"} —${availStr}`
      );
    }

    // Place ourselves adjacent to the target, on the side closer to us.
    const gridSize = scene.grid?.size ?? scene.grid ?? 100;
    const tgtCenterX = targetInfo.x + ((targetInfo.width || 1) * gridSize) / 2;
    const tgtCenterY = targetInfo.y + ((targetInfo.height || 1) * gridSize) / 2;
    const myCenterX = myToken.x + ((myToken.width || 1) * gridSize) / 2;
    const myCenterY = myToken.y + ((myToken.height || 1) * gridSize) / 2;
    const dx = myCenterX - tgtCenterX;
    const dy = myCenterY - tgtCenterY;
    const off = gridSize;
    if (Math.abs(dx) >= Math.abs(dy)) {
      destX = targetInfo.x + (dx >= 0 ? (targetInfo.width || 1) * gridSize : -((myToken.width || 1) * gridSize));
      destY = targetInfo.y;
    } else {
      destX = targetInfo.x;
      destY = targetInfo.y + (dy >= 0 ? (targetInfo.height || 1) * gridSize : -((myToken.height || 1) * gridSize));
    }
    // Snap to grid.
    destX = Math.round(destX / off) * off;
    destY = Math.round(destY / off) * off;
    // Clamp to scene bounds.
    destX = Math.max(0, Math.min(destX, (scene.width || 0) - off));
    destY = Math.max(0, Math.min(destY, (scene.height || 0) - off));
  }

  const fromX = myToken.x;
  const fromY = myToken.y;
  // {animate: false} — see moveToken() for rationale.
  await scene.updateEmbeddedDocuments(
      "Token",
      [{ _id: myToken.id, x: destX, y: destY }],
      { animate: false }
  );

  return {
    ok: true,
    tokenId: myToken.id,
    actorId: myToken.actorId,
    sceneId: scene.id,
    fromX, fromY,
    x: destX, y: destY,
    target,
    resolvedFrom,
  };
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

  // Idempotency: if a token for this actor already exists on the scene,
  // reuse it instead of stamping out a new copy. Without this every
  // application restart adds another duplicate token (and clogs the
  // ``move_actor_to`` "available targets" diagnostic with N copies of
  // each PC name).
  const existing = (scene.tokens ?? []).find?.(
    (t) => t.actorId === actor.id
        || (name && (t.name || "").toLowerCase() === String(name).toLowerCase())
  );
  if (existing) {
    // Backfill the playable-token settings on tokens spawned before
    // we knew to set them. This keeps players from staring at a black
    // map after upgrading the module mid-campaign.
    if (actor.type === "character") {
      const patch = {};
      if (!existing.actorLink) patch.actorLink = true;
      if (!existing.sight?.enabled) {
        patch.sight = {
          ...(existing.sight?.toObject?.() ?? existing.sight ?? {}),
          enabled: true,
          range: existing.sight?.range || 60,
        };
      }
      const friendly = CONST.TOKEN_DISPOSITIONS?.FRIENDLY ?? 1;
      if (existing.disposition !== friendly) patch.disposition = friendly;
      if (Object.keys(patch).length) {
        try {
          await scene.updateEmbeddedDocuments("Token", [
            { _id: existing.id, ...patch },
          ]);
          console.log(
            `AI DM Bridge: backfilled token settings on ${existing.name}`,
            patch
          );
        } catch (err) {
          console.warn("AI DM Bridge: token backfill failed", err);
        }
      }
    }
    return existing;
  }

  const tokenDoc = await actor.getTokenDocument({
    x,
    y,
    name: name ?? actor.name,
  });

  // Force settings that make the token actually playable for a player:
  //   - actorLink=true so the token IS the actor (HP, sheet, ownership)
  //   - sight.enabled with a 60-ft bright/dim radius so the player
  //     can see the map (otherwise it's all black under tokenVision).
  //   - disposition FRIENDLY for character actors so party members
  //     don't show up as red enemies.
  const tokenObj = tokenDoc.toObject();
  if (actor.type === "character") {
    tokenObj.actorLink = true;
    tokenObj.disposition =
      CONST.TOKEN_DISPOSITIONS?.FRIENDLY ?? 1;
    tokenObj.sight = {
      ...(tokenObj.sight || {}),
      enabled: true,
      range: tokenObj.sight?.range || 60,
      visionMode: tokenObj.sight?.visionMode || "basic",
    };
    // Some systems set brightness/contrast separately — leave any
    // existing values, only override the booleans we care about.
  }

  const created = await scene.createEmbeddedDocuments("Token", [tokenObj]);

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
