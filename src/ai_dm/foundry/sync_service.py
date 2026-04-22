from __future__ import annotations

from ai_dm.foundry.client import FoundryClient
from ai_dm.foundry.registry import FoundryRegistry
from ai_dm.foundry.results import unwrap_single_result
from ai_dm.foundry.snapshots import ActorSnapshot, SceneSnapshot, TokenSnapshot


class SyncService:
    """High-level helpers that talk to Foundry directly (no queue/executor).

    Whenever an entity is created we register it so that later AI-generated
    commands can refer to it by name. Pass ``client`` and ``registry`` from
    the application :class:`Container` to share state with the executor.
    """

    def __init__(
        self,
        client: FoundryClient | None = None,
        registry: FoundryRegistry | None = None,
    ) -> None:
        self.client = client or FoundryClient()
        self.registry = registry or FoundryRegistry()

    def create_scene(
            self,
            name: str,
            width: int = 4000,
            height: int = 3000,
            grid: int = 100,
    ) -> str:
        response = self.client.request({
            "type": "create_scene",
            "name": name,
            "width": width,
            "height": height,
            "grid": grid,
        })
        result = unwrap_single_result(response)
        scene_id = result["sceneId"]
        self.registry.register(
            "scene",
            scene_id,
            name=result.get("sceneName") or name,
            aliases=(name,),
        )
        return scene_id

    def activate_scene(self, scene_id: str) -> None:
        resolved = self._resolve("scene", scene_id)
        response = self.client.request({
            "type": "activate_scene",
            "scene_id": resolved,
        })
        unwrap_single_result(response)

    def create_actor(self, name: str, actor_type: str = "npc") -> str:
        response = self.client.request({
            "type": "create_actor",
            "name": name,
            "actor_type": actor_type,
        })
        result = unwrap_single_result(response)
        actor_id = result["actorId"]
        self.registry.register(
            "actor",
            actor_id,
            name=result.get("actorName") or name,
            aliases=(name,),
        )
        return actor_id

    def spawn_token(
            self,
            scene_id: str,
            actor_id: str,
            x: int,
            y: int,
            name: str | None = None,
    ) -> str:
        scene_id = self._resolve("scene", scene_id)
        actor_id = self._resolve("actor", actor_id)
        payload = {
            "type": "spawn_token",
            "scene_id": scene_id,
            "actor_id": actor_id,
            "x": x,
            "y": y,
        }
        if name is not None:
            payload["name"] = name

        response = self.client.request(payload)
        result = unwrap_single_result(response)
        token_id = result["tokenId"]
        self.registry.register(
            "token",
            token_id,
            name=name or token_id,
            aliases=(name,) if name else (),
            scene_id=scene_id,
        )
        return token_id

    def move_token(self, token_id: str, x: int, y: int) -> None:
        token_id = self._resolve("token", token_id)
        response = self.client.request({
            "type": "move_token",
            "token_id": token_id,
            "x": x,
            "y": y,
        })
        unwrap_single_result(response)

    # ------------------------------------------------------------------ #
    # Phase 3: inbound reads.
    # ------------------------------------------------------------------ #

    def pull_actor(self, actor_id: str) -> ActorSnapshot | None:
        actor_id = self._resolve("actor", actor_id)
        response = self.client.request({"type": "read_actor", "actor_id": actor_id})
        result = unwrap_single_result(response)
        snap = result.get("snapshot")
        if not isinstance(snap, dict):
            return None
        return ActorSnapshot.model_validate(snap)

    def pull_token(self, scene_id: str, token_id: str) -> TokenSnapshot | None:
        scene_id = self._resolve("scene", scene_id)
        token_id = self._resolve("token", token_id)
        response = self.client.request({
            "type": "read_token",
            "scene_id": scene_id,
            "token_id": token_id,
        })
        result = unwrap_single_result(response)
        snap = result.get("snapshot")
        if not isinstance(snap, dict):
            return None
        return TokenSnapshot.model_validate(snap)

    def pull_active_scene(self) -> SceneSnapshot | None:
        response = self.client.request({"type": "read_active_scene"})
        result = unwrap_single_result(response)
        snap = result.get("snapshot")
        if not isinstance(snap, dict):
            return None
        return SceneSnapshot.model_validate(snap)

    # ------------------------------------------------------------------ #

    def _resolve(self, kind: str, key: str) -> str:
        try:
            return self.registry.resolve(kind, key)  # type: ignore[arg-type]
        except Exception:
            # Unknown to the registry — assume caller already has a real id.
            return key
