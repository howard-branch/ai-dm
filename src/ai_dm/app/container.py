"""Application dependency container.

Owns the singletons that make up the runtime so each layer can be wired
together without reaching for module-level globals.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("ai_dm.app.container")

from ai_dm.ai.client import AIClient
from ai_dm.ai.context_builder import PromptContextBuilder
from ai_dm.ai.intent_parser import IntentParser
from ai_dm.ai.intent_router import IntentRouter
from ai_dm.ai.narrator import Narrator
from ai_dm.ai.planner import StoryPlanner
from ai_dm.audio.audio_queue import AudioQueue
from ai_dm.audio.narration_dispatcher import NarrationDispatcher
from ai_dm.audio.playback import play_bytes, play_stream
from ai_dm.audio.speech_input import SpeechInput
from ai_dm.audio.tts import NullBackend, TTSBackend, default_backend
from ai_dm.audio.voice_input_pump import VoiceControlBridge, VoiceInputPump
from ai_dm.audio.voices import VoiceProfile
from ai_dm.app.transcript_logger import TranscriptLogger
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
from ai_dm.foundry.relay_supervisor import RelaySupervisor
from ai_dm.foundry.socket_bridge import SocketBridge
from ai_dm.foundry.sync_service import SyncService
from ai_dm.foundry.validator import CommandValidator
from ai_dm.game.combat_machine import CombatMachine
from ai_dm.game.clock import Clock
from ai_dm.game.encounter_manager import EncounterManager
from ai_dm.game.location_loader import load_directory as load_locations_dir
from ai_dm.game.location_service import LocationService
from ai_dm.game.party_state import PartyState
from ai_dm.game.timeline import Timeline
from ai_dm.game.trigger_loader import load_triggers
from ai_dm.memory.npc_memory import NPCMemoryStore
from ai_dm.memory.relationships import RelationshipMatrix
from ai_dm.orchestration.actor_session import ActorSessionRegistry
from ai_dm.orchestration.command_router import CommandRouter
from ai_dm.orchestration.combat_projector import CombatProjector
from ai_dm.orchestration.event_bus import EventBus
from ai_dm.orchestration.npc_turn_driver import NPCTurnDriver
from ai_dm.orchestration.player_input_dispatcher import PlayerInputDispatcher
from ai_dm.orchestration.roll_request_dispatcher import RollRequestDispatcher
from ai_dm.orchestration.structured_intent_dispatcher import StructuredIntentDispatcher
from ai_dm.orchestration.triggers import TriggerEngine
from ai_dm.orchestration.turn_manager import TurnManager
from ai_dm.orchestration.xp_collector import XPCollector
from ai_dm.orchestration.xp_awarder import XPAwarder
from ai_dm.orchestration.interaction_effects import InteractionEffectsApplier
from ai_dm.orchestration.pc_attack_resolver import PCAttackResolver
from ai_dm.persistence.backups import BackupService
from ai_dm.persistence.campaign_store import CampaignStore
from ai_dm.persistence.roll_log import RollLog
from ai_dm.rules.action_resolver import ActionResolver
from ai_dm.rules.dm_rolls import DMRoller
from ai_dm.rules.engine import RulesEngine
from ai_dm.services.chapter_service import ChapterService


@dataclass
class ContainerConfig:
    relay_url: str = "ws://127.0.0.1:8765"
    queue_max_pending: int = 32
    # 30s — Foundry token movement / scene activation can take several
    # seconds in a real browser (animations, server roundtrips), and a
    # tight default produces "Timed out waiting for Foundry response"
    # errors even though the command actually succeeds.
    queue_default_timeout: float = 30.0
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
    # Speech-to-text input (push-to-talk via the runtime). Off by
    # default; the runtime enables it lazily on first use so tests
    # don't pull in audio deps.
    speech_input_enabled: bool = True
    intent_confidence_threshold: float = 0.6
    triggers_enabled: bool = True
    inbound_foundry_enabled: bool = True
    # Auto-start the WebSocket relay server in a background thread so a
    # single ``python -m ai_dm.main`` brings the whole stack up. Set to
    # False if you run ``scripts/run_foundry_replay.py`` separately.
    autostart_relay: bool = True
    relay_host: str = "127.0.0.1"
    relay_port: int = 8765
    # Authority — Python is authoritative by default; per-event policy
    # below decides what happens to GM-side mutations.
    authority_policy: AuthorityPolicy = field(default_factory=AuthorityPolicy)
    echo_suppression_ttl: float = 3.0
    # Rolls (Phase 4): player-facing roll prompts + DM-side rolls.
    rolls_enabled: bool = True
    rolls_timeout_s: float = 30.0
    rolls_on_timeout: str = "auto_roll"     # auto_roll | cancel | gm_prompt

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
    clock: Optional[Clock] = None
    tts: Optional[TTSBackend] = None
    audio_queue: Optional[AudioQueue] = None
    narration_dispatcher: Optional[NarrationDispatcher] = None
    speech_input: Optional[SpeechInput] = None
    voices: Optional[VoiceProfile] = None
    voice_pump: Optional[VoiceInputPump] = None
    voice_control: Optional[VoiceControlBridge] = None
    journal: Optional[JournalService] = None
    reconciler: Optional[Reconciler] = None
    socket_bridge: Optional[SocketBridge] = None
    arbiter: Optional[InboundArbiter] = None
    echo_suppressor: Optional[EchoSuppressor] = None
    relay_supervisor: Optional[RelaySupervisor] = None
    actor_sessions: Optional[ActorSessionRegistry] = None
    player_input_dispatcher: Optional[PlayerInputDispatcher] = None
    structured_intent_dispatcher: Optional[StructuredIntentDispatcher] = None
    roll_log: Optional[RollLog] = None
    dm_roller: Optional[DMRoller] = None
    roll_request_dispatcher: Optional[RollRequestDispatcher] = None
    party_state: Optional[PartyState] = None
    xp_collector: Optional[XPCollector] = None
    xp_awarder: Optional[XPAwarder] = None
    encounter_manager: Optional[EncounterManager] = None
    interaction_effects: Optional[InteractionEffectsApplier] = None
    pc_attack_resolver: Optional[PCAttackResolver] = None
    combat_projector: Optional[CombatProjector] = None
    npc_turn_driver: Optional[NPCTurnDriver] = None
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
        # Persistent conversation transcript — one file per session,
        # under <state_root>/transcripts/. Started immediately so any
        # subsequent player_input / narrator.output_ready events get
        # captured from turn 1.
        transcript_logger = TranscriptLogger(
            event_bus=event_bus, state_root=pack.state.root,
        )
        transcript_logger.start()
        client = FoundryClient(url=cfg.relay_url)
        # Bring the relay up *before* the client tries to dial it so
        # both inbound chat events and outbound commands work without
        # the user having to launch a second process.
        relay_supervisor: RelaySupervisor | None = None
        if cfg.autostart_relay and cfg.inbound_foundry_enabled:
            relay_supervisor = RelaySupervisor(host=cfg.relay_host, port=cfg.relay_port)
            try:
                relay_supervisor.start()
            except Exception:  # noqa: BLE001
                pass
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
            pack=pack,
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

        # Now that ``combat`` exists, wire a live actor lookup on the
        # action resolver so attack/damage resolution mutates real
        # combatants in ``combat.state.participants`` instead of
        # short-lived ActorRuleState stubs (which would silently
        # discard HP changes — the original "target doesn't take
        # damage" bug).
        #
        # Matching is intentionally fuzzy: the LLM/director frequently
        # emits the bare word the player typed (e.g. ``"grukk"``)
        # while the authored encounter declares the monster as
        # ``"mon.grukk"`` with display name ``"Grukk"``. Without the
        # fuzzy fallback every free-text attack lands on a stub at
        # hp=0 and the canvas-side ``apply_damage`` is rejected with
        # ``unknown_actor``.
        def _actor_lookup(key: str):
            if combat.state is None or not key:
                if combat.state is None:
                    logger.debug(
                        "npc_turn: actor_lookup(%r) — no encounter live "
                        "(combat.state is None); returning stub", key,
                    )
                return None
            participants = list(combat.state.participants)
            # 1) exact actor_id / token_id match.
            for p in participants:
                if p.actor_id == key or p.token_id == key:
                    return p
            # 2) case-insensitive match on actor_id, token_id, or name.
            norm = str(key).strip().lower()
            for p in participants:
                if (
                    str(p.actor_id).lower() == norm
                    or (p.token_id and str(p.token_id).lower() == norm)
                    or (getattr(p, "name", None) and str(p.name).lower() == norm)
                ):
                    logger.info(
                        "npc_turn: actor_lookup fuzzy-matched %r → %s "
                        "(name=%r) via case-insensitive id/name",
                        key, p.actor_id, getattr(p, "name", None),
                    )
                    return p
            # 3) suffix-after-dot match: ``"grukk"`` → ``"mon.grukk"``
            #    / ``"npc.grukk"``. Pick the foe-side combatant first
            #    so we don't accidentally damage a friendly NPC sharing
            #    the same short name.
            suffix_matches = [
                p for p in participants
                if "." in str(p.actor_id)
                and str(p.actor_id).split(".", 1)[1].lower() == norm
            ]
            suffix_matches.sort(key=lambda p: 0 if getattr(p, "team", None) == "foe" else 1)
            if suffix_matches:
                p = suffix_matches[0]
                logger.info(
                    "npc_turn: actor_lookup fuzzy-matched %r → %s "
                    "(name=%r) via suffix-after-dot",
                    key, p.actor_id, getattr(p, "name", None),
                )
                return p
            # 4) registry alias resolution: foundry id ↔ pack id.
            try:
                fid = registry.resolve("actor", key)
            except Exception:  # noqa: BLE001
                fid = None
            if fid:
                entry = registry.get("actor", fid)
                aliases = set(entry.aliases) if entry else set()
                aliases.add(fid)
                for p in participants:
                    if p.actor_id in aliases or p.token_id in aliases:
                        logger.info(
                            "npc_turn: actor_lookup matched %r → %s via "
                            "registry alias %s",
                            key, p.actor_id, fid,
                        )
                        return p
            logger.warning(
                "npc_turn: actor_lookup MISS for %r — no participant matches "
                "by id/token/name/suffix/alias. Live participants=%s. "
                "Damage will land on a STUB (hp=0) and Foundry projection "
                "will be rejected with unknown_actor.",
                key,
                [
                    {"actor_id": p.actor_id, "name": getattr(p, "name", None),
                     "token_id": p.token_id, "team": getattr(p, "team", None)}
                    for p in participants
                ],
            )
            return None

        action_resolver.actor_lookup = _actor_lookup
        # SpellResolver was constructed with the (None) lookup; refresh.
        try:
            action_resolver._spell_resolver.actor_lookup = _actor_lookup
        except Exception:  # noqa: BLE001
            pass
        # Expose to combat_projector so it can resolve 'grukk' → 'Grukk'
        # for the Foundry-side name match before dispatching apply_damage.
        combat._actor_lookup = _actor_lookup  # noqa: SLF001 — informal hook

        # Inject the combat machine into the prompt builder so the
        # narrator gets a ``combat`` block (round, current actor, HP
        # of every participant) when an encounter is live, and
        # describes the fight rather than ambient atmosphere.
        prompt_context.combat = combat

        # Party-level XP / level state. Mutated in place by:
        #   * XPCollector (combat banking + finalisation)
        #   * InteractionEffectsApplier (story / interaction xp)
        # Persisted via CampaignStore.
        party_state = PartyState()
        xp_collector = XPCollector(
            event_bus=event_bus,
            combat=combat,
            party_state=party_state,
            client=client,
        )
        xp_awarder = XPAwarder(
            event_bus=event_bus,
            party_state=party_state,
            client=client,
        )

        # In-game clock — constructed early so IntentRouter and the
        # trigger engine can both reach it. Save state is restored
        # below if a campaign save exists.
        clock = Clock(event_bus=event_bus)

        intent_router = IntentRouter(
            action_resolver=action_resolver,
            command_router=router,
            event_bus=event_bus,
            clock=clock,
            combat=combat,
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

        # Encounter facade — depends on combat + chapter_service so it
        # lives here. Used by InteractionEffectsApplier (below) to
        # drive starts_encounter / ends_encounter authored hooks.
        encounter_manager = EncounterManager(
            combat=combat, chapters=chapter_service, event_bus=event_bus,
            pack=pack, turn_manager=turn_manager, story_planner=story_planner,
            command_router=router,
        )
        interaction_effects = InteractionEffectsApplier(
            event_bus=event_bus,
            command_router=router,
            encounter_manager=encounter_manager,
        )

        # Resumes player chat-driven attacks deferred to the Foundry
        # roll dialog. Subscribed to ``roll.resolved``; runs the
        # ActionResolver with the player's d20 baked in.
        pc_attack_resolver = PCAttackResolver(
            event_bus=event_bus,
            action_resolver=action_resolver,
            turn_manager=turn_manager,
            combat=combat,
        )

        # Projects ``rules.damage_applied`` events into Foundry
        # ``apply_damage`` commands so the canvas/sheet reflect the
        # Python-side HP change.
        combat_projector = CombatProjector(
            event_bus=event_bus, command_router=router, combat=combat,
        )
        # Drives AI-controlled combatants on their highlighted turn so
        # the encounter doesn't stall waiting for a goblin to act.
        npc_turn_driver = NPCTurnDriver(
            event_bus=event_bus,
            combat=combat,
            rules=rules,
            turn_manager=turn_manager,
            client=client,
        )

        # Late-bind the travel deps now that the planner exists. The
        # router needs the pack (for nodes.json + scene_locations) and
        # the client (to push a fresh opening narration after a scene
        # change). Encounter_manager is also bound here so attack
        # intents can auto-start the scene's authored encounter when
        # combat isn't already live.
        intent_router.story_planner = story_planner
        intent_router.pack = pack
        intent_router.client = client
        intent_router.encounter_manager = encounter_manager

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

        # Speech input is constructed lazily-light: probing for ffmpeg
        # / arecord etc. is cheap and does not import any heavy deps.
        speech_input: SpeechInput | None = None
        if cfg.speech_input_enabled:
            try:
                speech_input = SpeechInput()
            except Exception:  # noqa: BLE001
                speech_input = None

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
                    "clock": clock.snapshot(),
                },
            )
            try:
                deps = {
                    "event_bus": event_bus,
                    "command_router": router,
                    "flags": flags,
                    "combat": combat,
                    "chapters": chapter_service,
                    "clock": clock,
                    "turn_manager": turn_manager,
                    "encounter_manager": encounter_manager,
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
            # Eagerly open the WebSocket so inbound /act chat events
            # are received from the moment Foundry connects, not just
            # after the first outbound command. Then keep retrying in
            # the background so the link survives relay restarts.
            client.try_connect()
            client.start_reconnect()
            # Refresh the census cache every 10s so timeout
            # diagnostics can name *who* is connected without an
            # in-line probe (which itself could stall during the same
            # hiccup that's causing the timeout).
            client.start_census_poll(interval=10.0)

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
            clock=clock,
            party_state=party_state,
        )

        if cfg.auto_load and campaign_store.save_path.exists():
            campaign_store.restore_into_runtime()

        # ---- Chat-driven multi-player input ---- #
        actor_sessions = ActorSessionRegistry(pack=pack)
        player_input_dispatcher = PlayerInputDispatcher(
            event_bus=event_bus,
            sessions=actor_sessions,
            client=client,
            prompt_context=prompt_context,
            director=None,  # wired in bootstrap.build_runtime after Director exists
        )
        structured_intent_dispatcher = StructuredIntentDispatcher(
            event_bus=event_bus,
            intent_router=intent_router,
            combat=combat,
            client=client,
            turn_manager=turn_manager,
        )
        # Roll-prompt pipeline (Phase 4). Owns the audit log under
        # ``<state_root>/logs/rolls.jsonl``, a DMRoller for hidden /
        # auto-roll rolls, and the dispatcher that emits
        # ``request_player_roll`` events to Foundry and resumes the
        # narration loop on ``foundry.player_roll_resolved``.
        roll_log = RollLog(state_root=pack.state.root)
        dm_roller = DMRoller(
            roll_log=roll_log,
            client=client,
            event_bus=event_bus,
        )
        roll_request_dispatcher = RollRequestDispatcher(
            event_bus=event_bus,
            client=client,
            roll_log=roll_log,
            dm_roller=dm_roller,
            timeout_s=cfg.rolls_timeout_s,
            on_timeout=cfg.rolls_on_timeout,
            enabled=cfg.rolls_enabled,
        )
        if cfg.inbound_foundry_enabled:
            player_input_dispatcher.start()
            structured_intent_dispatcher.start()
            xp_collector.start()
            xp_awarder.start()
            interaction_effects.start()
            combat_projector.start()
            npc_turn_driver.start()
            if cfg.rolls_enabled:
                roll_request_dispatcher.start()
                pc_attack_resolver.start()

        # ---- Voice input pump (host mic → foundry.player_input) ---- #
        # Constructed unconditionally so it can be flipped on at any
        # time from the Foundry browser via ``/voice on``. Auto-start
        # only when AI_DM_VOICE=1 *and* an actor is provided via
        # AI_DM_VOICE_ACTOR (otherwise we wait for the chat command,
        # which carries the player's controlled actor).
        import os as _os
        voice_pump = VoiceInputPump(
            event_bus=event_bus,
            speech_input=speech_input,
            audio_queue=audio_queue,
            client=client,
            actor_id=_os.environ.get("AI_DM_VOICE_ACTOR") or None,
        )
        voice_control = VoiceControlBridge(
            event_bus=event_bus, pump=voice_pump, client=client,
        )
        if cfg.inbound_foundry_enabled:
            voice_control.start()
        if (_os.environ.get("AI_DM_VOICE", "").strip().lower()
                in {"1", "true", "yes", "on"}):
            voice_pump.start()

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
            clock=clock,
            tts=tts,
            audio_queue=audio_queue,
            narration_dispatcher=narration_dispatcher,
            speech_input=speech_input,
            voices=voices,
            journal=journal,
            reconciler=reconciler,
            socket_bridge=socket_bridge,
            arbiter=arbiter,
            echo_suppressor=echo_suppressor,
            relay_supervisor=relay_supervisor,
            actor_sessions=actor_sessions,
            player_input_dispatcher=player_input_dispatcher,
            structured_intent_dispatcher=structured_intent_dispatcher,
            roll_log=roll_log,
            dm_roller=dm_roller,
            roll_request_dispatcher=roll_request_dispatcher,
            party_state=party_state,
            xp_collector=xp_collector,
            xp_awarder=xp_awarder,
            encounter_manager=encounter_manager,
            interaction_effects=interaction_effects,
            pc_attack_resolver=pc_attack_resolver,
            combat_projector=combat_projector,
            npc_turn_driver=npc_turn_driver,
            voice_pump=voice_pump,
            voice_control=voice_control,
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
        if self.player_input_dispatcher is not None:
            try:
                self.player_input_dispatcher.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.structured_intent_dispatcher is not None:
            try:
                self.structured_intent_dispatcher.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.roll_request_dispatcher is not None:
            try:
                self.roll_request_dispatcher.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.xp_collector is not None:
            try:
                self.xp_collector.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.xp_awarder is not None:
            try:
                self.xp_awarder.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.interaction_effects is not None:
            try:
                self.interaction_effects.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.combat_projector is not None:
            try:
                self.combat_projector.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.npc_turn_driver is not None:
            try:
                self.npc_turn_driver.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.voice_control is not None:
            try:
                self.voice_control.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.voice_pump is not None:
            try:
                self.voice_pump.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.client is not None:
            try:
                self.client.stop_reconnect()
            except Exception:  # noqa: BLE001
                pass
        if self.relay_supervisor is not None:
            try:
                self.relay_supervisor.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.queue is not None:
            self.queue.shutdown(wait=True)
