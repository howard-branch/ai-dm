from __future__ import annotations

from typing import Iterable

from ai_dm.ai.context_builder import PromptContextBuilder
from ai_dm.ai.intent_router import IntentRouter
from ai_dm.ai.narrator import Narrator
from ai_dm.ai.schemas import AIOutput, NPCDialogueLine
from ai_dm.memory.npc_memory import MemoryEvent, NPCMemoryStore
from ai_dm.orchestration.command_router import CommandRouter
from ai_dm.orchestration.event_bus import EventBus


class Director:
    def __init__(
        self,
        state_store,
        command_router: CommandRouter,
        *,
        narrator: Narrator | None = None,
        prompt_context: PromptContextBuilder | None = None,
        npc_memory: NPCMemoryStore | None = None,
        intent_router: IntentRouter | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.state_store = state_store
        self.narrator = narrator or Narrator()
        self.command_router = command_router
        self.prompt_context = prompt_context
        self.npc_memory = npc_memory
        self.intent_router = intent_router
        self.event_bus = event_bus

    def handle_player_input(
        self,
        player_input: str,
        *,
        focus_npcs: Iterable[str] | None = None,
        scene_id: str | None = None,
    ) -> AIOutput:
        context = self._build_context(player_input, focus_npcs=focus_npcs, scene_id=scene_id)
        result = self.narrator.narrate(player_input=player_input, context=context)

        self.state_store.apply_state_updates(result.state_updates)
        outcome = self.command_router.dispatch(result.commands)

        self._record_dialogue(result.dialogue)

        if outcome.rollback_errors:
            result.metadata["rollback_errors"] = list(outcome.rollback_errors)
        result.metadata["commands_ok"] = outcome.ok

        # Phase 3: emit a narrator.output_ready event so the audio
        # dispatcher (and any other listener) can react.
        if self.event_bus is not None:
            try:
                self.event_bus.publish(
                    "narrator.output_ready",
                    {
                        "narration": result.narration,
                        "spoken_dialogue": result.spoken_dialogue,
                        "dialogue": [d.model_dump() for d in result.dialogue],
                        "source": "narrator",
                    },
                )
            except Exception:  # noqa: BLE001
                pass

        return result

    # ------------------------------------------------------------------ #

    def _build_context(
        self,
        player_input: str,
        *,
        focus_npcs: Iterable[str] | None,
        scene_id: str | None,
    ) -> dict:
        if self.prompt_context is not None:
            # Prefer the rich builder. Inject our state_store on demand so we
            # don't have to wire it through the container.
            self.prompt_context.state_store = self.state_store
            return self.prompt_context.build(
                player_input,
                focus_npcs=focus_npcs,
                scene_id=scene_id,
            )
        try:
            return self.state_store.get_context()
        except Exception:  # noqa: BLE001
            return {}

    def _record_dialogue(self, dialogue: list[NPCDialogueLine]) -> None:
        if not dialogue or self.npc_memory is None:
            return
        for line in dialogue:
            self.npc_memory.record(
                line.npc_id,
                MemoryEvent(
                    kind="event",
                    text=line.text,
                    tags=["dialogue"] + ([line.tone] if line.tone else []),
                ),
            )
