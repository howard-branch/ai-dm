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
from ai_dm.rules.spell_resolver import SpellResolver
from ai_dm.rules.targeting import TargetSpec, resolve_targets

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


def _set_attr(actor: Any, name: str, value: Any) -> None:
    """Best-effort attribute assignment for duck-typed actor objects."""
    if actor is None:
        return
    try:
        setattr(actor, name, value)
    except Exception:  # noqa: BLE001 — frozen dataclass / unsupported attr
        pass


class ActionResolver:
    """Bridges intents to the rules engine."""

    def __init__(
        self,
        *,
        rules: RulesEngine | None = None,
        actor_lookup: ActorLookup | None = None,
        spell_catalog: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.rules = rules
        self.actor_lookup = actor_lookup
        self.spell_catalog = spell_catalog or {}
        self._spell_resolver = SpellResolver(
            rules=rules,
            actor_lookup=actor_lookup,
            spell_catalog=self.spell_catalog,
        )

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
        if kind == "cast_spell":
            return self._resolve_cast_spell(intent, ctx)
        if kind == "dash":
            return self._resolve_dash(intent)
        if kind == "disengage":
            return self._resolve_disengage(intent)
        if kind == "dodge":
            return self._resolve_dodge(intent)
        if kind == "help":
            return self._resolve_help(intent)
        if kind == "hide":
            return self._resolve_hide(intent, ctx)
        if kind == "ready":
            return self._resolve_ready(intent, ctx)
        if kind == "use_item":
            return self._resolve_use_item(intent, ctx)
        if kind == "end_turn":
            return self._resolve_end_turn(intent)
        if kind in ("move", "interact", "speak", "query_world", "meta"):
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
        # An attack consumes the actor's action and breaks stealth.
        self._consume_economy(attacker, action=True)
        _set_attr(attacker, "hidden", False)
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

    # ------------------------------------------------------------------ #
    # Combat action menu (5e SRD)
    # ------------------------------------------------------------------ #

    def _resolve_cast_spell(self, intent: Any, ctx: dict) -> ActionResolution:
        """Resolve a ``cast_spell`` intent by delegating to :class:`SpellResolver`.

        ``SpellResolver`` owns the full pipeline (eligibility, slot,
        targeting, attack/save, damage, effects, concentration, action
        economy). Here we just translate its :class:`SpellCastResult`
        into the :class:`ActionResolution` envelope the rest of the
        codebase consumes.
        """
        cast = self._spell_resolver.cast(intent, ctx)
        details: dict[str, Any] = {
            "spell": cast.spell,
            "level": cast.cast_level,
            "casting_time": cast.casting_time,
            "concentration": cast.concentration,
        }
        if cast.targeting is not None:
            details["targeting"] = cast.targeting
        if cast.slot_spent:
            details["slot_spent"] = True
        if cast.targets:
            details["targets"] = list(cast.targets)
        if cast.outcomes:
            details["outcomes"] = [o.to_dict() for o in cast.outcomes]
        return ActionResolution(
            type="cast_spell",
            actor_id=cast.actor_id,
            target_id=getattr(intent, "target_id", None),
            success=cast.success,
            summary=cast.summary,
            details=details,
        )

    def _resolve_dash(self, intent: Any) -> ActionResolution:
        actor_id = getattr(intent, "actor_id", None) or "player"
        actor = self._lookup(actor_id)
        if not self._consume_economy(actor, action=True):
            return ActionResolution(
                type="dash", actor_id=actor_id, success=False,
                summary="action already spent",
            )
        _set_attr(actor, "dashed", True)
        speed = int(getattr(actor, "speed", 30) or 30)
        return ActionResolution(
            type="dash", actor_id=actor_id, success=True,
            summary=f"dashes (speed +{speed} this turn)",
            details={"bonus_movement": speed},
        )

    def _resolve_disengage(self, intent: Any) -> ActionResolution:
        actor_id = getattr(intent, "actor_id", None) or "player"
        actor = self._lookup(actor_id)
        if not self._consume_economy(actor, action=True):
            return ActionResolution(
                type="disengage", actor_id=actor_id, success=False,
                summary="action already spent",
            )
        _set_attr(actor, "disengaging", True)
        return ActionResolution(
            type="disengage", actor_id=actor_id, success=True,
            summary="disengages — no opportunity attacks this turn",
        )

    def _resolve_dodge(self, intent: Any) -> ActionResolution:
        actor_id = getattr(intent, "actor_id", None) or "player"
        actor = self._lookup(actor_id)
        if not self._consume_economy(actor, action=True):
            return ActionResolution(
                type="dodge", actor_id=actor_id, success=False,
                summary="action already spent",
            )
        _set_attr(actor, "dodging", True)
        return ActionResolution(
            type="dodge", actor_id=actor_id, success=True,
            summary="dodges — attacks against them have disadvantage",
        )

    def _resolve_help(self, intent: Any) -> ActionResolution:
        actor_id = getattr(intent, "actor_id", None) or "player"
        target_id = getattr(intent, "target_id", None)
        if not target_id:
            return ActionResolution(
                type="help", actor_id=actor_id, success=False,
                summary="help requires a target",
            )
        actor = self._lookup(actor_id)
        if not self._consume_economy(actor, action=True):
            return ActionResolution(
                type="help", actor_id=actor_id, success=False,
                summary="action already spent",
            )
        _set_attr(actor, "helping_target", target_id)
        return ActionResolution(
            type="help", actor_id=actor_id, target_id=target_id, success=True,
            summary=f"helps {target_id} (advantage on next check/attack)",
        )

    def _resolve_hide(self, intent: Any, ctx: dict) -> ActionResolution:
        """Hide as an action — optionally rolled vs. a passive Perception DC.

        ``ctx`` may carry ``stealth_modifier`` (int) and ``dc`` (int).
        Without a rules engine we record the intent and mark the actor
        hidden optimistically.
        """
        actor_id = getattr(intent, "actor_id", None) or "player"
        actor = self._lookup(actor_id)
        if not self._consume_economy(actor, action=True):
            return ActionResolution(
                type="hide", actor_id=actor_id, success=False,
                summary="action already spent",
            )

        success = True
        details: dict[str, Any] = {}
        if self.rules is not None and "dc" in ctx:
            stealth_mod = int(ctx.get("stealth_modifier", 0))
            dc = int(ctx.get("dc", 10))
            rule_actor = ActorRuleState(actor_id=actor_id, name=actor_id)
            check = self.rules.ability_check(rule_actor, modifier=stealth_mod, dc=dc)
            success = check.success
            details["check"] = check.to_dict()

        if success:
            _set_attr(actor, "hidden", True)
        return ActionResolution(
            type="hide", actor_id=actor_id, success=success,
            summary="hides successfully" if success else "fails to hide",
            details=details,
        )

    def _resolve_ready(self, intent: Any, ctx: dict) -> ActionResolution:
        """Ready an action: reserves both your action and your reaction.

        ``ctx`` describes the trigger and the deferred sub-action:
            {
              "trigger": "when the goblin steps through the door",
              "action": "attack",  # or "cast_spell" / "use_item" / ...
              "payload": { ... },  # forwarded when the trigger fires
              "spell_level": 1,    # optional — slot reserved at this level
            }
        Per RAW, a readied spell consumes its slot immediately and
        requires concentration until released; we mirror that here.
        """
        actor_id = getattr(intent, "actor_id", None) or "player"
        actor = self._lookup(actor_id)
        trigger = str(ctx.get("trigger") or getattr(intent, "notes", "") or "")
        sub_action = str(ctx.get("action") or "attack")
        payload = dict(ctx.get("payload") or {})
        spell_level = ctx.get("spell_level")

        if not self._consume_economy(actor, action=True):
            return ActionResolution(
                type="ready", actor_id=actor_id, success=False,
                summary="action already spent",
            )
        if not self._consume_economy(actor, reaction=True):
            # Roll back the action so the caller can retry next turn.
            _set_attr(actor, "action_used", False)
            return ActionResolution(
                type="ready", actor_id=actor_id, success=False,
                summary="reaction already spent",
            )

        # Readied spells: pre-spend the slot.
        if (
            sub_action == "cast_spell"
            and spell_level is not None
            and actor is not None
            and hasattr(actor, "spend_slot")
        ):
            if not actor.spend_slot(int(spell_level)):
                return ActionResolution(
                    type="ready", actor_id=actor_id, success=False,
                    summary=f"no level-{spell_level} slot to ready",
                )

        readied = {
            "trigger": trigger,
            "action": sub_action,
            "payload": payload,
            "spell_level": spell_level,
        }
        _set_attr(actor, "readied_action", readied)
        return ActionResolution(
            type="ready", actor_id=actor_id, success=True,
            summary=f"readies {sub_action}: {trigger or '(no trigger specified)'}",
            details=readied,
        )

    def _resolve_use_item(self, intent: Any, ctx: dict) -> ActionResolution:
        """Use an item.

        By default this consumes the actor's action; pass
        ``ctx={"economy": "bonus" | "free"}`` for items that don't.
        """
        actor_id = getattr(intent, "actor_id", None) or "player"
        actor = self._lookup(actor_id)
        item = (
            getattr(intent, "target_id", None)
            or ctx.get("item")
            or "item"
        )
        economy = str(ctx.get("economy", "action")).lower()
        if economy == "bonus":
            ok = self._consume_economy(actor, bonus_action=True)
        elif economy == "free":
            ok = True
        else:
            ok = self._consume_economy(actor, action=True)
        if not ok:
            return ActionResolution(
                type="use_item", actor_id=actor_id, success=False,
                summary=f"cannot use {item}: {economy} already spent",
            )
        return ActionResolution(
            type="use_item", actor_id=actor_id, target_id=str(item),
            success=True,
            summary=f"uses {item}",
            details={"item": item, "economy": economy},
        )

    def _resolve_end_turn(self, intent: Any) -> ActionResolution:
        """End the actor's turn explicitly. No mechanical state changes
        — turn cleanup happens in :class:`CombatMachine.end_turn`."""
        actor_id = getattr(intent, "actor_id", None) or "player"
        return ActionResolution(
            type="end_turn", actor_id=actor_id, success=True,
            summary=f"{actor_id} ends their turn",
        )

    # ------------------------------------------------------------------ #
    # Action-economy helpers
    # ------------------------------------------------------------------ #

    def _consume_economy(
        self,
        actor: Any,
        *,
        action: bool = False,
        bonus_action: bool = False,
        reaction: bool = False,
    ) -> bool:
        """Mark the requested action-economy slice as spent.

        Returns ``False`` if the slice was already spent on this turn.
        Silently no-ops when ``actor`` is ``None`` or lacks the
        relevant attribute (legacy ``ActorRuleState`` callers).
        """
        if actor is None:
            return True
        if action and getattr(actor, "action_used", False):
            return False
        if bonus_action and getattr(actor, "bonus_action_used", False):
            return False
        if reaction and getattr(actor, "reaction_used", False):
            return False
        if action and hasattr(actor, "action_used"):
            actor.action_used = True
        if bonus_action and hasattr(actor, "bonus_action_used"):
            actor.bonus_action_used = True
        if reaction and hasattr(actor, "reaction_used"):
            actor.reaction_used = True
        return True

    def _lookup(self, actor_id: str) -> ActorRuleState | None:
        if self.actor_lookup is None:
            return None
        try:
            return self.actor_lookup(actor_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("actor lookup failed for %s: %s", actor_id, exc)
            return None
