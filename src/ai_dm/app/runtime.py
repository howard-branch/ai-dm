"""Interactive runtime — a simple REPL command loop.

Reads a prompt from the user, hands it to the :class:`Director`, prints
the resulting narration / dialogue, and reports whether the dispatched
Foundry commands succeeded. The Director itself is responsible for
pushing commands through the :class:`CommandRouter` into Foundry, so we
don't need to do anything extra here to keep the VTT in sync.
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import threading

logger = logging.getLogger("ai_dm.app.runtime")

_BANNER = (
    "AI DM runtime started. Type a prompt and press Enter.\n"
    "Type :help for the command list.\n"
)
_REMOTE_BANNER = (
    "AI DM runtime started in Foundry-driven mode.\n"
    "Open the Foundry world in your browser and use chat (`/act ...`)\n"
    "to play. The local terminal will only print logs and shutdown\n"
    "messages — press Ctrl-C to stop the server.\n"
    "Set AI_DM_LOCAL_REPL=1 to re-enable the local text REPL.\n"
)
_HELP = (
    "  <text>          Send <text> to the DM as your character's input.\n"
    "  :scene <id>     Set the active scene id used for context.\n"
    "  :scene          Clear the active scene id.\n"
    "  :char           Show the active player character.\n"
    "  :char <id>      Switch to a different character (loaded from\n"
    "                  the campaign pack's characters/ directory).\n"
    "  :mute           Disable voice narration for this session.\n"
    "  :unmute         Re-enable voice narration.\n"
    "  :voice          Hands-free mode: speak naturally; recording\n"
    "                  auto-stops on silence and the transcript is\n"
    "                  sent to the DM. Press Ctrl-C or say one of the\n"
    "                  stop-phrases to return to text input.\n"
    "  :listen [secs]  Push-to-talk: records from the mic, then sends\n"
    "                  the transcript as your input. With no argument,\n"
    "                  records until you press Enter again.\n"
    "  :mic            Show speech-input diagnostics (mic + STT backend).\n"
    "  :help           Show this help.\n"
    "  :quit / :exit   Exit the loop.\n"
)

# Spoken phrases that drop the user back to the keyboard from hands-free
# voice mode. Matched case-insensitively against the *normalised*
# transcript (punctuation stripped, collapsed whitespace).
_VOICE_EXIT_PHRASES = (
    "stop listening",
    "stop voice",
    "end voice",
    "exit voice",
    "exit voice mode",
    "leave voice mode",
    "quit voice",
    "voice off",
)
# Spoken phrases that quit the runtime entirely.
_VOICE_QUIT_PHRASES = (
    "quit game",
    "exit game",
    "end session",
    "shut down",
    "shutdown",
)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text or "")).strip().lower()


class Runtime:
    def __init__(self, director, container=None, *, voice_on_start: bool = False) -> None:
        self.director = director
        self.container = container
        self._scene_id: str | None = None
        self._voice_on_start = voice_on_start
        self._shutdown = threading.Event()

    # ------------------------------------------------------------------ #

    def start(self) -> None:
        # Default mode: stay quietly in the background so player input
        # arrives via the connected Foundry browser (relay → SocketBridge
        # → PlayerInputDispatcher → Director). Set ``AI_DM_LOCAL_REPL=1``
        # to bring back the legacy stdin loop for offline debugging.
        local_repl = (os.environ.get("AI_DM_LOCAL_REPL", "").strip().lower()
                      in {"1", "true", "yes", "on"})
        print(_BANNER if local_repl else _REMOTE_BANNER)
        self._announce_character()
        try:
            if local_repl:
                if self._voice_on_start:
                    self._voice_loop()
                self._loop()
            else:
                self._wait_for_shutdown()
        finally:
            self.shutdown()

    def _wait_for_shutdown(self) -> None:
        """Block until SIGINT / SIGTERM. Foundry-driven mode."""
        def _handle(_signum, _frame):  # noqa: ANN001
            self._shutdown.set()

        # Best-effort: only the main thread can install signal handlers.
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handle)
            except (ValueError, OSError):
                pass
        try:
            while not self._shutdown.is_set():
                # Long but bounded wait so KeyboardInterrupt on platforms
                # that don't deliver to ``Event.wait`` still surfaces.
                if self._shutdown.wait(timeout=1.0):
                    return
        except KeyboardInterrupt:
            self._shutdown.set()
        print("\n[shutting down]")

    def _loop(self) -> None:
        while True:
            prompt = f"{self._character_name() or 'you'}> "
            try:
                raw = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print()  # newline after ^C / ^D
                return

            if not raw:
                continue

            if raw.startswith(":"):
                if self._handle_meta(raw):
                    return
                continue

            self._handle_prompt(raw)

    # ------------------------------------------------------------------ #

    def _handle_meta(self, line: str) -> bool:
        """Return True if the loop should exit."""
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in (":quit", ":exit", ":q"):
            return True
        if cmd == ":help":
            print(_HELP)
            return False
        if cmd == ":scene":
            self._scene_id = arg or None
            print(f"[scene set to {self._scene_id!r}]")
            return False

        if cmd == ":char":
            if not arg:
                self._show_character()
            else:
                self._switch_character(arg)
            return False

        if cmd in (":mute", ":unmute"):
            enable = cmd == ":unmute"
            dispatcher = getattr(self.container, "narration_dispatcher", None)
            queue = getattr(self.container, "audio_queue", None)
            if dispatcher is None or queue is None:
                print("[audio not configured]")
                return False
            dispatcher.enabled = enable
            if enable:
                dispatcher.start()
                queue.start()
                print("[voice narration: on]")
            else:
                queue.interrupt()
                print("[voice narration: off]")
            return False

        if cmd == ":mic":
            speech = getattr(self.container, "speech_input", None) if self.container else None
            if speech is None:
                print("[speech input not configured]")
                return False
            status = speech.status()
            print(
                "[mic={mic_tool} ({mic_ok}) | stt={stt} ({stt_ok})]".format(
                    mic_tool=status["mic_tool"] or "—",
                    mic_ok="ok" if status["mic_available"] else "missing",
                    stt=status["transcribe_backend"],
                    stt_ok="ok" if status["transcribe_available"] else "missing",
                )
            )
            return False

        if cmd == ":voice":
            self._voice_loop()
            return False

        if cmd == ":listen":
            secs: float | None = None
            if arg:
                try:
                    secs = float(arg)
                except ValueError:
                    print(f"[bad duration: {arg!r}]")
                    return False
            text = self._push_to_talk(seconds=secs)
            if text:
                self._handle_prompt(text)
            return False

        print(f"[unknown command {cmd!r}; try :help]")
        return False

    def _handle_prompt(self, prompt: str) -> None:
        try:
            result = self.director.handle_player_input(
                prompt, scene_id=self._scene_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("director failed")
            print(f"[error: {exc}]")
            return

        narration = (result.narration or "").strip()
        if narration:
            print(f"\nDM> {narration}\n")

        for line in result.dialogue:
            tone = f" ({line.tone})" if line.tone else ""
            print(f"  {line.npc_id}{tone}: {line.text}")

        commands_ok = result.metadata.get("commands_ok")
        n_commands = len(result.commands)
        if n_commands:
            status = "ok" if commands_ok else "FAILED"
            print(f"[foundry: dispatched {n_commands} command(s) — {status}]")
        rb = result.metadata.get("rollback_errors")
        if rb:
            print(f"[rollback errors: {rb}]")

    # ------------------------------------------------------------------ #
    # Character helpers
    # ------------------------------------------------------------------ #

    def _character(self) -> dict | None:
        ctx = getattr(self.container, "prompt_context", None) if self.container else None
        return getattr(ctx, "character", None) if ctx is not None else None

    def _character_name(self) -> str | None:
        sheet = self._character()
        if not sheet:
            return None
        return sheet.get("name") or sheet.get("id")

    def _announce_character(self) -> None:
        sheet = self._character()
        if not sheet:
            print("[no player character loaded — use :char <id> to load one]")
            self._explain_missing_character()
            return
        name = sheet.get("name") or sheet.get("id") or "?"
        klass = sheet.get("class")
        suffix = f" — {klass}" if klass else ""
        print(f"[playing as {name}{suffix}]")

    def _explain_missing_character(self) -> None:
        """Print why no PC is loaded so the user can fix it without trawling logs."""
        pack = getattr(self.container, "pack", None) if self.container else None
        if pack is None:
            print("  reason: no campaign pack resolved — check config/settings.yaml "
                  "(campaigns.active) and that you're running from the repo root.")
            return
        start = (getattr(pack.manifest, "start", None) or {})
        pc_id = start.get("player_character")
        if not pc_id:
            print(f"  reason: campaign {pack.slug!r} has no start.player_character "
                  f"in its manifest ({pack.root}/campaign.yaml). Add a "
                  f"'start: {{ player_character: <id> }}' block, or use "
                  f":char <id> to load one manually.")
            return
        live = pack.state.characters / f"{pc_id}.json"
        seed = pack.paths.characters_seed / f"{pc_id}.json"
        print(f"  reason: pc_id={pc_id!r} but no sheet was loadable.")
        print(f"    live: {live} (exists={live.exists()})")
        print(f"    seed: {seed} (exists={seed.exists()})")
        if not live.exists() and not seed.exists():
            print("  fix: run with --new-character (or AI_DM_NEW_CHARACTER=1) and "
                  "complete the wizard in the connected Foundry browser, or drop "
                  "a hand-crafted seed JSON at the path above.")
        else:
            print("  fix: the file exists but failed to load — check the log "
                  "above for a JSON parse error, then re-run.")

    def _show_character(self) -> None:
        sheet = self._character()
        if not sheet:
            print("[no character loaded]")
            return
        print(json.dumps(sheet, indent=2))

    def _switch_character(self, char_id: str) -> None:
        if self.container is None or self.container.pack is None:
            print("[no campaign pack available]")
            return
        pack = self.container.pack
        candidates = [
            pack.state.characters / f"{char_id}.json",
            pack.paths.characters_seed / f"{char_id}.json",
        ]
        for path in candidates:
            if not path.exists():
                continue
            try:
                sheet = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                print(f"[failed to load {path}: {exc}]")
                return
            ctx = getattr(self.container, "prompt_context", None)
            if ctx is None:
                print("[prompt context unavailable]")
                return
            ctx.character = sheet
            self._announce_character()
            return
        print(f"[no character file found for {char_id!r} in {pack.state.characters} or {pack.paths.characters_seed}]")

    # ------------------------------------------------------------------ #
    # Voice input
    # ------------------------------------------------------------------ #

    def _push_to_talk(self, *, seconds: float | None = None) -> str:
        speech = getattr(self.container, "speech_input", None) if self.container else None
        if speech is None:
            print("[speech input not configured]")
            return ""
        if not speech.recorder.is_available():
            print("[no microphone tool found — install ffmpeg, arecord or parec]")
            return ""
        if not speech.transcriber.is_available():
            print("[no speech-to-text backend — set OPENAI_API_KEY or install faster-whisper]")
            return ""

        if seconds is not None:
            print(f"[recording {seconds:.1f}s …]")
            text = speech.listen_for(seconds)
        else:
            print("[recording … press Enter to stop]")
            stop = speech.begin()
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                print()
            print("[transcribing …]")
            text = stop()

        text = (text or "").strip()
        if not text:
            print("[no speech detected]")
            return ""
        print(f"[heard] {text}")
        return text

    # ------------------------------------------------------------------ #
    # Hands-free voice loop
    # ------------------------------------------------------------------ #

    def _voice_loop(self) -> None:
        """Continuously: wait for TTS to finish, listen, transcribe, send.

        No keyboard input is required. The user leaves voice mode by:

        * speaking one of :data:`_VOICE_EXIT_PHRASES` (e.g. "stop
          listening") — drops back to the text REPL;
        * speaking a :data:`_VOICE_QUIT_PHRASES` phrase — exits the
          program (handled by the caller via the returned bool from
          ``_handle_meta``-style flow);
        * pressing Ctrl-C — drops back to the text REPL;
        * an EOF on stdin (rare in interactive use).
        """
        speech = getattr(self.container, "speech_input", None) if self.container else None
        if speech is None:
            print("[speech input not configured]")
            return
        if not speech.recorder.is_available():
            print("[no microphone tool found — install ffmpeg, arecord or parec]")
            return
        if not speech.transcriber.is_available():
            print("[no speech-to-text backend — set OPENAI_API_KEY or install faster-whisper]")
            return

        audio_queue = getattr(self.container, "audio_queue", None)

        print(
            "\n[voice mode] speak naturally — recording auto-stops on silence.\n"
            "  Say 'stop listening' (or press Ctrl-C) to return to text input.\n"
            "  Say 'quit game' to exit the program.\n"
        )

        # Ctrl-C inside this loop should drop us back to the text REPL,
        # not kill the process. We use a cancel event so the in-flight
        # ffmpeg recording can be torn down promptly.
        cancel = threading.Event()

        try:
            while True:
                # 1. Wait for any DM speech to finish so we don't
                #    immediately re-transcribe our own narration.
                if audio_queue is not None:
                    try:
                        audio_queue.join(timeout=60.0)
                    except Exception:  # noqa: BLE001
                        pass

                # 2. Open the mic.
                cancel.clear()
                print("[listening …]", flush=True)

                def _on_speech() -> None:
                    print("[heard you, recording …]", flush=True)

                try:
                    text = speech.listen_vad(
                        cancel=cancel, on_speech_start=_on_speech
                    )
                except KeyboardInterrupt:
                    print("\n[voice mode off]")
                    return

                text = (text or "").strip()
                if not text:
                    # Silence / nothing detected — quietly try again.
                    continue

                print(f"[heard] {text}")

                # 3. Honour spoken control phrases.
                norm = _normalise(text)
                if any(p in norm for p in _VOICE_EXIT_PHRASES):
                    print("[voice mode off]")
                    return
                if any(p in norm for p in _VOICE_QUIT_PHRASES):
                    print("[goodbye]")
                    raise SystemExit(0)

                # 4. Hand the transcript to the director just like any
                #    typed prompt. The narration_dispatcher will start
                #    speaking; the next loop iteration's join() will
                #    wait for it before re-opening the mic.
                self._handle_prompt(text)
        except KeyboardInterrupt:
            cancel.set()
            print("\n[voice mode off]")

    # ------------------------------------------------------------------ #

    def shutdown(self) -> None:
        if self.container is not None:
            try:
                self.container.shutdown()
            except Exception:  # noqa: BLE001
                logger.exception("container shutdown failed")

