"""Resolve a structured player intent into a mechanical outcome.

Phase-3 ``ActionResolver`` is intent-driven: it consumes a
:class:`ai_dm.ai.intent_schemas.PlayerIntent` and produces an
:class:`ActionResolution` describing what actually happened. The narrator
then describes the resolution in prose.

For backwards compatibility a ``resolve(text)`` overload still accepts a
raw string and returns a freeform stub.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ai_dm.rules.engine import ActorRuleState, RulesEngine

logger = logging.getLogger("ai_dm.rules.resolver")


@dataclass
class ActionResolution:
    type: str
    actor_id: str | None = None
    target_id: str | None = None
    success: bool = True
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "actor_id": self.actor_id,
            "target_id": self.target_id,
            "success": self.success,
            "summary": self.summary,
            "details": self.details,
        }


ActorLookup = Callable[[str], "ActorRuleState | None"]


class ActionResolver:
    """Bridges intents to the rules engine."""

    def __init__(
        self,
        *,
        rules: RulesEngine | None = None,
        actor_lookup: ActorLookup | None = None,
    ) -> None:
        self.rules = rules
        self.actor_lookup = actor_lookup

    def resolve(self, intent: Any, ctx: dict | None = None) -> Any:
        if isinstance(intent, str):
            # Legacy contract — returns a plain dict.
            return {"type": "freeform", "text": intent}
        return self.resolve_intent(intent, ctx or {})

    def resolve_intent(self, intent: Any, ctx: dict) -> ActionResolution:
        kind = (
            getattr(intent, "type", None)
            or (intent.get("type") if isinstance(intent, dict) else None)
        )
        if kind is None:
            return ActionResolution(type="freeform", summary="no intent type")

        if kind == "skill_check":
            return self._resolve_check(intent)
        if kind == "attack":
            return self._resolve_attack(intent, ctx)
        if kind in ("move", "interact", "speak", "use_item", "query_world", "meta"):
            return ActionResolution(
                type=kind,
                actor_id=getattr(intent, "actor_id", None),
                target_id=getattr(intent, "target_id", None),
                summary=getattr(intent, "raw_text", "") or kind,
            )
        return ActionResolution(type="freeform", summary=str(intent))

    # ------------------------------------------------------------------ #

    def _resolve_check(self, intent: Any) -> ActionResolution:
        if self.rules is None:
            return ActionResolution(
                type="skill_check", success=False, summary="rules engine unavailable"
            )
        actor_id = getattr(intent, "actor_id", None) or "player"
        actor = self._lookup(actor_id) or ActorRuleState(actor_id=actor_id, name=actor_id)
        modifier = int(getattr(intent, "modifier", 0) or 0)
        dc = int(getattr(intent, "dc", 10) or 10)
        result = self.rules.ability_check(actor, modifier=modifier, dc=dc)
        return ActionResolution(
            type="skill_check",
            actor_id=actor_id,
            success=result.success,
            summary=(
                f"{getattr(intent, 'skill', 'check')} DC {dc}: "
                f"rolled {result.total} → {'success' if result.success else 'failure'}"
            ),
            details=result.to_dict(),
        )

    def _resolve_attack(self, intent: Any, ctx: dict) -> ActionResolution:
        if self.rules is None:
            return ActionResolution(
                type="attack", success=False, summary="rules engine unavailable"
            )
        actor_id = getattr(intent, "actor_id", None) or "player"
        target_id = getattr(intent, "target_id", None)
        if not target_id:
            return ActionResolution(
                type="attack", actor_id=actor_id, success=False, summary="no target"
            )
        attacker = self._lookup(actor_id) or ActorRuleState(actor_id=actor_id, name=actor_id)
        target = self._lookup(target_id) or ActorRuleState(actor_id=target_id, name=target_id)
        attack_mod = int(ctx.get("attack_modifier", 0))
        damage_dice = str(ctx.get("damage_dice", "1d6"))
        damage_bonus = int(ctx.get("damage_bonus", 0))
        damage_type = str(ctx.get("damage_type", "slashing"))

        atk = self.rules.attack(attacker, target, attack_modifier=attack_mod)
        damage_total = 0
        damage_details: dict | None = None
        if atk.hit:
            dmg = self.rules.damage(
                target,
                dice=damage_dice,
                bonus=damage_bonus,
                damage_type=damage_type,
                crit=atk.crit,
            )
            damage_total = dmg.total
            damage_details = dmg.to_dict()
            self.rules.apply_damage(target, dmg.total)
        return ActionResolution(
            type="attack",
            actor_id=actor_id,
            target_id=target_id,
            success=atk.hit,
            summary=(
                f"{actor_id} → {target_id}: "
                + ("HIT" if atk.hit else "miss")
                + (f" for {damage_total}" if atk.hit else "")
                + (" (CRIT)" if atk.crit else "")
            ),
            details={
                "attack": atk.to_dict(),
                "damage": damage_details,
                "target_hp": target.hp,
            },
        )

    def _lookup(self, actor_id: str) -> ActorRuleState | None:
        if self.actor_lookup is None:
            return None
        try:
            return self.actor_lookup(actor_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("actor lookup failed for %s: %s", actor_id, exc)
            return None
