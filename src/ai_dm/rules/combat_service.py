"""Backwards-compat shim for the very early combat helper.

Real combat lives in :mod:`ai_dm.game.combat_machine` and the rules
math in :mod:`ai_dm.rules.engine`.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("ai_dm.rules.combat")


class CombatService:
    def begin(self) -> None:
        logger.info("CombatService.begin called (legacy shim)")
