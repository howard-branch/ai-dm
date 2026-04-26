from __future__ import annotations

from typing import Iterable

from ai_dm.ai.context_builder import PromptContextBuilder
from ai_dm.ai.intent_parser import IntentParser
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
        intent_parser: IntentParser | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.state_store = state_store
        self.narrator = narrator or Narrator()
        self.command_router = command_router
        self.prompt_context = prompt_context
        self.npc_memory = npc_memory
        self.intent_router = intent_router
        self.intent_parser = intent_parser
        self.event_bus = event_bus

    def handle_player_input(
        self,
        player_input: str,
        *,
        focus_npcs: Iterable[str] | None = None,
        scene_id: str | None = None,
        actor_id: str | None = None,
    ) -> AIOutput:
        # Phase 3.1: parse the utterance for a mechanical intent (move,
        # attack, skill check, …) BEFORE running the narrator. Movement
        # / interact intents are dispatched directly through the
        # IntentRouter so the token actually moves on the canvas; the
        # narrator still runs afterwards to describe the action.
        self._maybe_dispatch_intent(player_input, scene_id=scene_id, actor_id=actor_id)

        context = self._build_context(player_input, focus_npcs=focus_npcs, scene_id=scene_id)
        result = self.narrator.narrate(player_input=player_input, context=context)

        # Phase 3: emit narrator.output_ready as early as possible so the
        # audio dispatcher can begin synthesising the first sentence
        # while we run the (often slower) state-update / Foundry-command
        # / NPC-memory bookkeeping below. This shaves hundreds of ms off
        # the time-to-first-sound after the LLM responds.
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

        self.state_store.apply_state_updates(result.state_updates)
        outcome = self.command_router.dispatch(result.commands)

        self._record_dialogue(result.dialogue)

        if outcome.rollback_errors:
            result.metadata["rollback_errors"] = list(outcome.rollback_errors)
        result.metadata["commands_ok"] = outcome.ok

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

    # ------------------------------------------------------------------ #
    # Intent dispatch
    # ------------------------------------------------------------------ #

    _MECHANICAL_INTENT_TYPES = ("move", "interact", "attack", "skill_check")

    def _maybe_dispatch_intent(
        self,
        text: str,
        *,
        scene_id: str | None,
        actor_id: str | None,
    ) -> None:
        """Run the IntentParser and route mechanical intents through the
        IntentRouter so they reach the canvas (move tokens etc).

        No-op when no parser/router is wired (e.g. in unit tests with a
        bare Director). Failures are swallowed so they never break the
        narrator flow that runs immediately after.
        """
        if self.intent_parser is None or self.intent_router is None:
            return
        try:
            intent = self.intent_parser.parse(text, ctx={
                "scene_id": scene_id,
                "actor_id": actor_id,
            })
        except Exception:  # noqa: BLE001
            return
        if intent.type not in self._MECHANICAL_INTENT_TYPES:
            return
        # Override the parser's default actor_id ("player") with the
        # real Foundry actor id from the chat event.
        if actor_id:
            intent.actor_id = actor_id
        try:
            self.intent_router.handle(
                intent, ctx={"scene_id": scene_id, "actor_id": actor_id}
            )
        except Exception:  # noqa: BLE001
            return

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
