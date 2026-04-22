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
from pathlib import Path

logger = logging.getLogger("ai_dm.app.runtime")

_BANNER = (
    "AI DM runtime started. Type a prompt and press Enter.\n"
    "Type :help for the command list.\n"
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
    "  :help           Show this help.\n"
    "  :quit / :exit   Exit the loop.\n"
)


class Runtime:
    def __init__(self, director, container=None) -> None:
        self.director = director
        self.container = container
        self._scene_id: str | None = None

    # ------------------------------------------------------------------ #

    def start(self) -> None:
        print(_BANNER)
        self._announce_character()
        try:
            self._loop()
        finally:
            self.shutdown()

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
            return
        name = sheet.get("name") or sheet.get("id") or "?"
        klass = sheet.get("class")
        suffix = f" — {klass}" if klass else ""
        print(f"[playing as {name}{suffix}]")

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

    def shutdown(self) -> None:
        if self.container is not None:
            try:
                self.container.shutdown()
            except Exception:  # noqa: BLE001
                logger.exception("container shutdown failed")

