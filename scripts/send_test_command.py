from ai_dm.foundry.sync_service import SyncService
import time



def main() -> None:
    foundry = SyncService()

    scene_id = foundry.create_scene("Bridge Test Scene")
    print("scene_id =", scene_id)

    foundry.activate_scene(scene_id)
    print("scene activated")

    actor_id = foundry.create_actor("Bridge Goblin", actor_type="npc")
    print("actor_id =", actor_id)

    token_id = foundry.spawn_token(
        scene_id=scene_id,
        actor_id=actor_id,
        x=1000,
        y=800,
        name="Bridge Goblin",
    )
    print("token_id =", token_id)

    for attempt in range(5):
    	try:
        	foundry.move_token(token_id, 2400, 2900)
        	break
    	except RuntimeError:
        	if attempt == 4:
            		raise
        	time.sleep(0.5)
    print("token moved")


if __name__ == "__main__":
    main()
