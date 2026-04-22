export async function activateScene(sceneId) {
  const scene = game.scenes?.get(sceneId);
  if (!scene) {
    throw new Error(`Scene not found: ${sceneId}`);
  }

  await scene.activate();
  return scene;
}

export async function createScene(name, width = 4000, height = 3000, grid = 100) {
  const existing = game.scenes?.find((s) => s.name === name);
  if (existing) {
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
  };
}
