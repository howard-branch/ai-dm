"""Application dependency container.

Owns the singletons that make up the runtime so each layer can be wired
together without reaching for module-level globals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ai_dm.ai.client import AIClient
from ai_dm.ai.context_builder import PromptContextBuilder
from ai_dm.ai.intent_parser import IntentParser
from ai_dm.ai.intent_router import IntentRouter
from ai_dm.ai.narrator import Narrator
from ai_dm.ai.planner import StoryPlanner
from ai_dm.audio.audio_queue import AudioQueue
from ai_dm.audio.narration_dispatcher import NarrationDispatcher
from ai_dm.audio.playback import play_bytes, play_stream
from ai_dm.audio.tts import NullBackend, TTSBackend, default_backend
from ai_dm.audio.voices import VoiceProfile
from ai_dm.campaign.pack import CampaignPack, seed_characters
from ai_dm.foundry.authority import (
    AuthorityPolicy,
    EchoSuppressor,
    InboundArbiter,
)
from ai_dm.foundry.batch_executor import BatchExecutor, CommandInverter
from ai_dm.foundry.client import FoundryClient
from ai_dm.foundry.command_queue import GLOBAL_SCOPE, SerialCommandQueue
from ai_dm.foundry.journal import JournalService
from ai_dm.foundry.reconciler import Reconciler
from ai_dm.foundry.registry import FoundryRegistry
from ai_dm.foundry.socket_bridge import SocketBridge
from ai_dm.foundry.sync_service import SyncService
from ai_dm.foundry.validator import CommandValidator
from ai_dm.game.combat_machine import CombatMachine
from ai_dm.game.location_loader import load_directory as load_locations_dir
from ai_dm.game.location_service import LocationService
from ai_dm.game.timeline import Timeline
from ai_dm.game.trigger_loader import load_triggers
from ai_dm.memory.npc_memory import NPCMemoryStore
from ai_dm.memory.relationships import RelationshipMatrix
from ai_dm.orchestration.command_router import CommandRouter
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.orchestration.triggers import TriggerEngine
from ai_dm.orchestration.turn_manager import TurnManager
from ai_dm.persistence.backups import BackupService
from ai_dm.persistence.campaign_store import CampaignStore
from ai_dm.rules.action_resolver import ActionResolver
from ai_dm.rules.engine import RulesEngine
from ai_dm.services.chapter_service import ChapterService


@dataclass
class ContainerConfig:
    relay_url: str = "ws://127.0.0.1:8765"
    queue_max_pending: int = 32
    queue_default_timeout: float = 10.0
    default_scope: str = GLOBAL_SCOPE
    # ---- Campaign pack (preferred) -------------------------------- #
    # When set, all campaign-dependent paths (chapters, prompts,
    # triggers, voices, locations, characters seed, saves, backups,
    # memory) are resolved through this pack. When ``None``, a legacy
    # pack is synthesised from ``saves_dir`` + ``campaign_assets``
    # below for backward compatibility.
    pack: CampaignPack | None = None
    seed_characters_on_build: bool = True
    # ---- Legacy path fields (used only when ``pack is None``) ----- #
    saves_dir: Path = Path("data/saves")
    campaign_assets: Path = Path("assets/campaign")
    rules_assets: Path = Path("assets/rules")
    max_backups: int = 20
    auto_load: bool = False
    auto_save_on_shutdown: bool = False
    npc_memory_max_events: int = 50
    # Phase 3
    audio_enabled: bool = False  # off by default for tests / headless
    audio_backend: TTSBackend | None = None  # injectable for tests
    edge_voice: str = "en-GB-SoniaNeural"
    intent_confidence_threshold: float = 0.6
    triggers_enabled: bool = True
    inbound_foundry_enabled: bool = True
    # Authority — Python is authoritative by default; per-event policy
    # below decides what happens to GM-side mutations.
    authority_policy: AuthorityPolicy = field(default_factory=AuthorityPolicy)
    echo_suppression_ttl: float = 3.0

    def resolved_pack(self) -> CampaignPack:
        """Return the active pack, building a legacy one if needed."""
        if self.pack is not None:
            return self.pack
        return CampaignPack.from_legacy_layout(
            campaign_assets=self.campaign_assets,
            saves_dir=self.saves_dir,
        )


@dataclass
class Container:
    """Holds the wired-up runtime singletons."""

    config: ContainerConfig = field(default_factory=ContainerConfig)
    pack: Optional[CampaignPack] = None
    registry: FoundryRegistry = field(default_factory=FoundryRegistry)
    event_bus: EventBus = field(default_factory=EventBus)
    client: Optional[FoundryClient] = None
    queue: Optional[SerialCommandQueue] = None
    validator: Optional[CommandValidator] = None
    inverter: Optional[CommandInverter] = None
    executor: Optional[BatchExecutor] = None
    sync_service: Optional[SyncService] = None
    location_service: Optional[LocationService] = None
    npc_memory: Optional[NPCMemoryStore] = None
    relationships: Optional[RelationshipMatrix] = None
    prompt_context: Optional[PromptContextBuilder] = None
    narrator: Optional[Narrator] = None
    combat: Optional[CombatMachine] = None
    turn_manager: Optional[TurnManager] = None
    command_router: Optional[CommandRouter] = None
    campaign_store: Optional[CampaignStore] = None
    # Phase 3
    rules: Optional[RulesEngine] = None
    action_resolver: Optional[ActionResolver] = None
    intent_parser: Optional[IntentParser] = None
    intent_router: Optional[IntentRouter] = None
    triggers: Optional[TriggerEngine] = None
    timeline: Optional[Timeline] = None
    chapter_service: Optional[ChapterService] = None
    story_planner: Optional[StoryPlanner] = None
    tts: Optional[TTSBackend] = None
    audio_queue: Optional[AudioQueue] = None
    narration_dispatcher: Optional[NarrationDispatcher] = None
    voices: Optional[VoiceProfile] = None
    journal: Optional[JournalService] = None
    reconciler: Optional[Reconciler] = None
    socket_bridge: Optional[SocketBridge] = None
    arbiter: Optional[InboundArbiter] = None
    echo_suppressor: Optional[EchoSuppressor] = None
    token_state: dict[str, Any] = field(default_factory=dict)
    scene_state: dict[str, Any] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)
    actor_state: dict[str, Any] = field(default_factory=dict)
    foundry_journals: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(cls, config: ContainerConfig | None = None) -> "Container":
        cfg = config or ContainerConfig()
        pack = cfg.resolved_pack()
        # Make sure writable state dirs exist before any subsystem
        # touches them. Best-effort — read-only filesystems are fine.
        try:
            pack.state.ensure()
        except Exception:  # noqa: BLE001
            pass
        # Seed live characters from pack on first run (idempotent).
        if cfg.seed_characters_on_build:
            try:
                seed_characters(pack)
            except Exception:  # noqa: BLE001
                pass
        registry = FoundryRegistry()
        event_bus = EventBus()
        client = FoundryClient(url=cfg.relay_url)
        queue = SerialCommandQueue(
            client,
            max_pending=cfg.queue_max_pending,
            default_timeout=cfg.queue_default_timeout,
        )
        validator = CommandValidator(registry)
        inverter = CommandInverter(queue, registry)
        executor = BatchExecutor(queue, registry, inverter=inverter)
        sync_service = SyncService(client=client, registry=registry)

        location_service = LocationService(registry=registry)
        try:
            location_service.load_many(
                load_locations_dir(pack.paths.locations)
            )
        except Exception:  # noqa: BLE001
            pass

        npc_memory = NPCMemoryStore(max_events_per_npc=cfg.npc_memory_max_events)
        relationships = RelationshipMatrix()

        # ---- Phase 3: rules ---- #
        rules = RulesEngine.from_assets(cfg.rules_assets, event_bus=event_bus)
        action_resolver = ActionResolver(rules=rules)

        # ---- Intents ---- #
        intent_parser = IntentParser(
            client=AIClient(),
            confidence_threshold=cfg.intent_confidence_threshold,
        )
        # Will be wired with command_router below once it exists.

        prompt_context = PromptContextBuilder(
            state_store=None,
            npc_memory=npc_memory,
            relationships=relationships,
            location_service=location_service,
        )

        schema_path = pack.paths.output_schema
        system_path = pack.paths.system_narrator
        narrator = Narrator(
            client=AIClient(),
            system_prompt_path=system_path if system_path.exists() else None,
            schema_path=schema_path if schema_path.exists() else None,
        )

        router = CommandRouter(
            validator=validator,
            executor=executor,
            location_service=location_service,
            default_scope=cfg.default_scope,
        )

        combat = CombatMachine(event_bus=event_bus, command_router=router)
        turn_manager = TurnManager(combat=combat)

        intent_router = IntentRouter(
            action_resolver=action_resolver,
            command_router=router,
            event_bus=event_bus,
        )

        # ---- Phase 3: planner ---- #
        chapter_service = ChapterService(base=pack.paths.chapters)
        timeline = Timeline()
        story_planner = StoryPlanner(
            chapters=chapter_service,
            timeline=timeline,
            event_bus=event_bus,
        )
        prompt_context.story_planner = story_planner

        # ---- Audio (TTS) ---- #
        tts = cfg.audio_backend or (
            default_backend(edge_voice=cfg.edge_voice) if cfg.audio_enabled else NullBackend()
        )
        voices = VoiceProfile.load(
            pack.paths.voices,
            default_voice=cfg.edge_voice,
        )
        # Sink: a callable for the buffered path, plus a ``.stream``
        # attribute that the AudioQueue will prefer when the backend
        # exposes a ``stream(text, voice)`` generator. Streaming pipes
        # MP3 chunks straight to ffplay's stdin so playback starts
        # within ~200ms instead of waiting for the full clip.
        def _sink(_item, audio):  # noqa: ANN001
            play_bytes(audio)

        def _sink_stream(_item, chunks):  # noqa: ANN001
            play_stream(chunks)

        _sink.stream = _sink_stream  # type: ignore[attr-defined]

        audio_queue = AudioQueue(
            tts,
            sink=_sink,
            autostart=cfg.audio_enabled,
        )
        narration_dispatcher = NarrationDispatcher(
            event_bus=event_bus,
            queue=audio_queue,
            voices=voices,
            enabled=cfg.audio_enabled,
        )
        if cfg.audio_enabled:
            narration_dispatcher.start()

        # ---- Triggers ---- #
        flags: dict[str, Any] = {}
        actor_state: dict[str, Any] = {}
        foundry_journals: dict[str, Any] = {}

        triggers: TriggerEngine | None = None
        if cfg.triggers_enabled:
            triggers = TriggerEngine(
                event_bus,
                context_provider=lambda: {
                    "flags": flags,
                    "actors": actor_state,
                    "chapter": story_planner.state.current_chapter,
                },
            )
            try:
                deps = {
                    "event_bus": event_bus,
                    "command_router": router,
                    "flags": flags,
                    "combat": combat,
                }
                triggers.load(load_triggers(pack.paths.triggers, deps=deps))
            except Exception:  # noqa: BLE001
                pass

        # ---- Foundry sync extensions ---- #
        journal = JournalService(client=client)
        reconciler = Reconciler(
            sync=sync_service,
            registry=registry,
            event_bus=event_bus,
            command_router=router,
            actor_state_provider=actor_state.get,
        )
        socket_bridge = SocketBridge(client, event_bus)
        if cfg.inbound_foundry_enabled:
            socket_bridge.connect()

        # ---- Authority arbitration ---- #
        # Python is authoritative. The arbiter consumes ``foundry.*``
        # inbound events, deduplicates self-echoes (via the suppressor
        # primed by every outbound dispatch), then absorbs/reverts/
        # ignores per ``cfg.authority_policy``.
        echo_suppressor = EchoSuppressor(ttl=cfg.echo_suppression_ttl)
        token_state: dict[str, Any] = {}
        scene_state: dict[str, Any] = {}
        arbiter = InboundArbiter(
            event_bus=event_bus,
            suppressor=echo_suppressor,
            registry=registry,
            policy=cfg.authority_policy,
            actor_state=actor_state,
            token_state=token_state,
            scene_state=scene_state,
            command_router=router,
        )
        if cfg.inbound_foundry_enabled:
            arbiter.start()
        # Prime the suppressor for every outbound write we send.
        router.pre_dispatch_hooks.append(arbiter.prime_outbound)

        # ---- Persistence ---- #
        backups = BackupService(pack.state.backups, max_backups=cfg.max_backups)
        campaign_store = CampaignStore(
            base=pack.state.saves,
            registry=registry,
            location_service=location_service,
            npc_memory=npc_memory,
            relationships=relationships,
            combat=combat,
            backups=backups,
            flags=flags,
            story_planner=story_planner,
            triggers=triggers,
            foundry_journals=foundry_journals,
            actor_state=actor_state,
        )

        if cfg.auto_load and campaign_store.save_path.exists():
            campaign_store.restore_into_runtime()

        return cls(
            config=cfg,
            pack=pack,
            registry=registry,
            event_bus=event_bus,
            client=client,
            queue=queue,
            validator=validator,
            inverter=inverter,
            executor=executor,
            sync_service=sync_service,
            location_service=location_service,
            npc_memory=npc_memory,
            relationships=relationships,
            prompt_context=prompt_context,
            narrator=narrator,
            combat=combat,
            turn_manager=turn_manager,
            command_router=router,
            campaign_store=campaign_store,
            rules=rules,
            action_resolver=action_resolver,
            intent_parser=intent_parser,
            intent_router=intent_router,
            triggers=triggers,
            timeline=timeline,
            chapter_service=chapter_service,
            story_planner=story_planner,
            tts=tts,
            audio_queue=audio_queue,
            narration_dispatcher=narration_dispatcher,
            voices=voices,
            journal=journal,
            reconciler=reconciler,
            socket_bridge=socket_bridge,
            arbiter=arbiter,
            echo_suppressor=echo_suppressor,
            flags=flags,
            actor_state=actor_state,
            token_state=token_state,
            scene_state=scene_state,
            foundry_journals=foundry_journals,
        )

    def shutdown(self) -> None:
        if self.config.auto_save_on_shutdown and self.campaign_store is not None:
            try:
                self.campaign_store.save()
            except Exception:  # noqa: BLE001
                pass
        if self.narration_dispatcher is not None:
            try:
                self.narration_dispatcher.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.audio_queue is not None:
            try:
                self.audio_queue.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
        if self.triggers is not None:
            try:
                self.triggers.shutdown()
            except Exception:  # noqa: BLE001
                pass
        if self.socket_bridge is not None:
            try:
                self.socket_bridge.disconnect()
            except Exception:  # noqa: BLE001
                pass
        if self.arbiter is not None:
            try:
                self.arbiter.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.queue is not None:
            self.queue.shutdown(wait=True)
