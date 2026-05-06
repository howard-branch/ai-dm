"""DM-side dice roller.

When the AI DM (planner / triggers / hidden NPC behaviour) needs to
roll without prompting a player, the result is computed authoritatively
in Python by :class:`DiceRoller` / :func:`d20_test`, written to the
:class:`RollLog`, and (optionally) echoed to Foundry chat as a
``chat_roll`` event so the GM can see what just happened.

This module never blocks on Foundry: the roll happens locally and the
chat echo is fire-and-forget. Visibility defaults to ``"gm"`` so hidden
checks (monster stealth, secret perception, weather tables) only
whisper the GM.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from ai_dm.orchestration.event_bus import EventBus
from ai_dm.persistence.roll_log import RollLog, RollRecord
from ai_dm.rules.dice import Advantage, DiceRoller, d20_test

logger = logging.getLogger("ai_dm.rules.dm_rolls")


class DMRoller:
    """Authoritative DM-side roller with audit + chat echo."""

    def __init__(
        self,
        *,
        roll_log: RollLog | None = None,
        client: Any = None,                    # FoundryClient for chat echo
        event_bus: EventBus | None = None,
        roller: DiceRoller | None = None,
    ) -> None:
        self.roll_log = roll_log
        self.client = client
        self.event_bus = event_bus
        self.roller = roller or DiceRoller()

    # ------------------------------------------------------------------ #

    def roll(
        self,
        *,
        actor_id: str | None = None,
        roll_type: str = "raw",                # skill | save | ability | attack | damage | raw
        key: str | None = None,
        formula: str | None = None,
        modifier: int = 0,
        dc: int | None = None,
        ac: int | None = None,
        advantage: Advantage = "normal",
        visibility: str = "gm",
        scene_id: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> RollRecord:
        """Roll, log, echo. Returns the :class:`RollRecord`.

        For d20 tests (``skill`` / ``save`` / ``ability`` / ``attack``),
        omit ``formula`` and pass ``modifier`` + ``dc``/``ac``; this
        delegates to :func:`d20_test` so advantage/crit/fumble work.
        Otherwise pass ``formula`` (e.g. ``"2d6+3"`` for a damage roll).
        """
        request_id = request_id or f"dm-{uuid.uuid4().hex[:12]}"
        if roll_type in ("skill", "save", "ability", "attack") and not formula:
            test = d20_test(
                self.roller,
                modifier=modifier,
                dc=dc,
                ac=ac,
                advantage=advantage,
                is_attack=(roll_type == "attack"),
            )
            raw = test.raw  # type: ignore[union-attr]
            record = RollRecord(
                request_id=request_id,
                source="dm",
                actor_id=actor_id,
                roll_type=roll_type,
                key=key,
                formula=raw.expression if raw else "1d20",
                total=test.total,
                modifier=test.modifier,
                rolls=list(raw.rolls) if raw else [test.roll],
                kept=list(raw.kept) if raw else [test.roll],
                advantage=test.advantage,
                crit=test.crit,
                fumble=test.fumble,
                dc=dc,
                ac=ac,
                success=test.success,
                visibility=visibility,
                scene_id=scene_id,
                reason=reason,
            )
        else:
            expr = formula or "1d20"
            rr = self.roller.roll(expr, advantage=advantage)
            success: bool | None = None
            if dc is not None:
                success = rr.total >= dc
            record = RollRecord(
                request_id=request_id,
                source="dm",
                actor_id=actor_id,
                roll_type=roll_type,
                key=key,
                formula=expr,
                total=rr.total,
                modifier=rr.modifier,
                rolls=list(rr.rolls),
                kept=list(rr.kept),
                advantage=rr.advantage,
                crit=rr.crit,
                fumble=rr.fumble,
                dc=dc,
                ac=ac,
                success=success,
                visibility=visibility,
                scene_id=scene_id,
                reason=reason,
            )

        if self.roll_log is not None:
            self.roll_log.append(record)
        if self.event_bus is not None:
            try:
                self.event_bus.publish("roll.resolved", {
                    "record": record.to_dict(),
                    "source": "dm",
                })
            except Exception:  # noqa: BLE001
                pass
        self._echo_to_chat(record)
        return record

    # ------------------------------------------------------------------ #

    def _echo_to_chat(self, record: RollRecord) -> None:
        if self.client is None:
            return
        # The Foundry side renders ``chat_roll`` as a styled message
        # (whispered to the GM when visibility=="gm"). The Python value
        # is authoritative; the JS side just displays it.
        payload = {
            "request_id": record.request_id,
            "actor_id": record.actor_id,
            "roll_type": record.roll_type,
            "key": record.key,
            "formula": record.formula,
            "total": record.total,
            "modifier": record.modifier,
            "rolls": list(record.rolls),
            "kept": list(record.kept),
            "advantage": record.advantage,
            "crit": record.crit,
            "fumble": record.fumble,
            "dc": record.dc,
            "ac": record.ac,
            "success": record.success,
            "visibility": record.visibility,
            "reason": record.reason,
        }
        try:
            self.client.send_event("chat_roll", payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("chat_roll send failed: %s", exc)

