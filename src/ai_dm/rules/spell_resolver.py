"""SpellResolver — full life-cycle for ``cast_spell`` intents.

Where :class:`ai_dm.rules.engine.RulesEngine` provides the d20 +
damage primitives, :class:`SpellResolver` orchestrates the whole
spell pipeline:

1. **Eligibility** — does the caster *know* / have *prepared* this
   spell, and is the requested cast level ≥ the spell's base level?
   (Skipped for actors that haven't been hydrated with spell-knowledge
   data, so legacy/duck-typed combatants still work.)
2. **Spell-slot accounting** — cantrips and rituals are free; leveled
   spells consume a slot at the requested cast level via
   :meth:`CombatantState.spend_slot`.
3. **Range / target resolution** — uses :class:`TargetSpec` +
   :func:`resolve_targets` to expand ``self`` / ``single`` / ``multi``
   / ``radius`` / ``sphere`` / ``cube`` / ``cone`` / ``line`` / ``point``.
4. **Attack roll *or* saving throw branch** — driven by the spell
   record's ``attack_roll`` (``"melee"``/``"ranged"``) or ``save``
   block (``{"ability": "dex", "half_on_save": true}``); auto-resolve
   when neither is present (e.g. *magic missile*).
5. **Damage rolling + application** — sums every entry in
   ``record["damage"]["parts"]``, halves on a successful save when
   ``half_on_save`` is set, doubles dice on a critical hit, applies
   resistances/vulnerabilities/immunities via :meth:`RulesEngine.damage`,
   then commits via :meth:`RulesEngine.apply_damage`.
6. **Effects / conditions** — applies any ``record["effects"]``
   (``[{"condition": "frightened", "on": "fail"|"hit"|"always"}, ...]``)
   to the resolved targets.
7. **Concentration** — starts (or replaces) :class:`Concentration` on
   the caster when the spell record (or ``ctx``) declares it.
8. **Action-economy consumption** — ``casting_time`` ("action" /
   "bonus" / "reaction") drives which slot is charged; the slot is
   refunded if the cast bails out *after* it was reserved.
9. **Stealth break** — ``hidden=False`` on the caster.

The resolver is intentionally side-effect heavy on success: it
mutates :class:`CombatantState` in place. ``ActionResolver`` delegates
to :meth:`SpellResolver.cast` from ``_resolve_cast_spell`` so existing
callers keep their current contract.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ai_dm.rules.engine import RulesEngine
from ai_dm.rules.targeting import TargetSpec, resolve_targets

logger = logging.getLogger(__name__)

ActorLookup = Callable[[str], Any]


# --------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------- #


@dataclass
class TargetOutcome:
    """Per-target slice of a spell cast."""

    target_id: str
    hit: bool | None = None          # set when an attack roll was made
    crit: bool = False
    save_success: bool | None = None  # set when a saving throw was rolled
    damage: int = 0
    damage_type: str | None = None
    effects_applied: list[str] = field(default_factory=list)
    hp_after: int | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "hit": self.hit,
            "crit": self.crit,
            "save_success": self.save_success,
            "damage": self.damage,
            "damage_type": self.damage_type,
            "effects_applied": list(self.effects_applied),
            "hp_after": self.hp_after,
            "note": self.note,
        }


@dataclass
class SpellCastResult:
    """Structured outcome of :meth:`SpellResolver.cast`.

    Mirrors the shape ``ActionResolver`` packages into ``ActionResolution``,
    but is also useful in isolation (e.g. AI planners scoring a cast).
    """

    success: bool
    actor_id: str
    spell: str | None
    cast_level: int
    casting_time: str
    economy_slot: str  # "action" / "bonus" / "reaction" / "free" (ritual)
    concentration: bool
    slot_spent: bool
    summary: str
    targets: list[str] = field(default_factory=list)
    outcomes: list[TargetOutcome] = field(default_factory=list)
    targeting: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "spell": self.spell,
            "level": self.cast_level,
            "casting_time": self.casting_time,
            "economy": self.economy_slot,
            "concentration": self.concentration,
            "slot_spent": self.slot_spent,
            "targets": list(self.targets),
            "outcomes": [o.to_dict() for o in self.outcomes],
            "targeting": self.targeting,
            "error": self.error,
        }


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #


def _set_attr(actor: Any, name: str, value: Any) -> None:
    if actor is not None and hasattr(actor, name):
        try:
            setattr(actor, name, value)
        except Exception:  # noqa: BLE001 — frozen / read-only fields
            pass


def _is_caster_hydrated(actor: Any) -> bool:
    """Return True if the actor has any spell-knowledge data set.

    Used to gate the eligibility check: legacy/duck-typed combatants
    with empty cantrips/known/prepared lists are treated as
    *unconfigured* and pass through.
    """
    if actor is None:
        return False
    for attr in ("cantrips", "known_spells", "prepared_spells"):
        seq = getattr(actor, attr, None)
        if seq:
            return True
    if getattr(actor, "casting_style", None):
        return True
    return False


def _normalise_casting_time(s: str | None) -> str:
    """Map raw catalog strings ("1 bonus action", "reaction", …) to slots."""
    if not s:
        return "action"
    low = str(s).lower()
    if "bonus" in low:
        return "bonus"
    if "reaction" in low:
        return "reaction"
    if "free" in low:
        return "free"
    return "action"


def _is_concentration(record: dict | None, ctx: dict) -> bool:
    if "concentration" in ctx:
        return bool(ctx["concentration"])
    if not record:
        return False
    if record.get("concentration") is True:
        return True
    duration = str(record.get("duration") or "").lower()
    return "concentration" in duration


# --------------------------------------------------------------------- #
# SpellResolver
# --------------------------------------------------------------------- #


class SpellResolver:
    """End-to-end resolver for ``cast_spell`` intents."""

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

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def cast(self, intent: Any, ctx: dict | None = None) -> SpellCastResult:
        ctx = dict(ctx or {})
        actor_id = getattr(intent, "actor_id", None) or "player"
        actor = self._lookup(actor_id)

        spell_id = (
            getattr(intent, "spell", None)
            or ctx.get("spell")
            or ctx.get("spell_id")
        )
        record = self.spell_catalog.get(str(spell_id)) if spell_id else None
        spell_level = int((record or {}).get("level", 0))
        cast_level = int(ctx.get("level", spell_level) or 0)
        casting_time_raw = str(
            ctx.get("casting_time")
            or (record or {}).get("casting_time")
            or "action"
        )
        economy_slot = _normalise_casting_time(casting_time_raw)
        ritual = bool(ctx.get("ritual", False))
        if ritual:
            economy_slot = "free"
        concentrates = _is_concentration(record, ctx)

        result = SpellCastResult(
            success=False,
            actor_id=actor_id,
            spell=spell_id,
            cast_level=cast_level,
            casting_time=casting_time_raw,
            economy_slot=economy_slot,
            concentration=concentrates,
            slot_spent=False,
            summary="",
        )

        # 1) Eligibility -------------------------------------------------- #
        elig_err = self._check_eligibility(
            actor, spell_id, spell_level=spell_level,
            cast_level=cast_level, ritual=ritual,
        )
        if elig_err:
            result.error = elig_err
            result.summary = f"cannot cast {spell_id or 'spell'}: {elig_err}"
            return result

        # 2) Targeting ---------------------------------------------------- #
        spec = self._build_spec(record, ctx)
        resolved = None
        if spec is not None:
            resolved = resolve_targets(
                spec, intent=intent, ctx=ctx,
                actor=actor, actor_lookup=self.actor_lookup,
            )
            result.targeting = resolved.to_dict()
            if not resolved.success:
                result.error = resolved.error
                result.summary = (
                    f"cannot cast {spell_id or 'spell'}: {resolved.error}"
                )
                return result

        # 3) Spell slot --------------------------------------------------- #
        slot_spent = False
        if cast_level > 0 and not ritual and actor is not None and hasattr(actor, "spend_slot"):
            if not actor.spend_slot(cast_level):
                result.error = f"no level-{cast_level} slot available"
                result.summary = result.error
                return result
            slot_spent = True
        result.slot_spent = slot_spent

        # 4) Action economy ---------------------------------------------- #
        if not self._consume_economy(actor, economy_slot):
            # Refund the slot we just spent so caller can retry next turn.
            if slot_spent and actor is not None:
                self._refund_slot(actor, cast_level)
                result.slot_spent = False
            result.error = f"{economy_slot} already spent"
            result.summary = f"cannot cast {spell_id or 'spell'}: {result.error}"
            return result

        # 5) Per-target attack/save/damage/effects ----------------------- #
        target_ids = list(resolved.actor_ids) if resolved else []
        outcomes = self._apply_to_targets(
            actor=actor, target_ids=target_ids, record=record, ctx=ctx,
        )
        result.targets = target_ids
        result.outcomes = outcomes

        # 6) Concentration ----------------------------------------------- #
        if concentrates and actor is not None:
            self._start_concentration(
                actor, spell_id=spell_id, record=record, target_ids=target_ids,
            )

        # 7) Stealth break ----------------------------------------------- #
        _set_attr(actor, "hidden", False)

        result.success = True
        result.summary = (
            f"casts {spell_id or 'a spell'}"
            + (f" at level {cast_level}" if cast_level > 0 else "")
            + (" (concentration)" if concentrates else "")
        )
        return result

    # ------------------------------------------------------------------ #
    # Pipeline steps
    # ------------------------------------------------------------------ #

    def _check_eligibility(
        self,
        actor: Any,
        spell_id: Any,
        *,
        spell_level: int,
        cast_level: int,
        ritual: bool,
    ) -> str | None:
        """Return an error string if the cast is illegal, else ``None``."""
        # Cast level must be at least the spell's base level (cantrips
        # are level 0 → cast_level 0 is fine).
        if spell_level > cast_level:
            return (
                f"cannot cast level-{spell_level} spell at slot level {cast_level}"
            )

        if not _is_caster_hydrated(actor) or not spell_id:
            # Legacy / unconfigured caster — skip the knowledge gate.
            return None

        if ritual:
            if hasattr(actor, "can_ritual_cast") and not actor.can_ritual_cast(str(spell_id)):
                return f"{spell_id} cannot be cast as a ritual"
            return None

        if hasattr(actor, "is_prepared") and not actor.is_prepared(str(spell_id)):
            return f"{spell_id} is not known/prepared"
        return None

    def _build_spec(self, record: dict | None, ctx: dict) -> TargetSpec | None:
        spec_override = ctx.get("target_spec")
        if isinstance(spec_override, TargetSpec):
            return spec_override
        if isinstance(spec_override, dict):
            return TargetSpec.from_catalog({"targeting": spec_override})
        if record:
            return TargetSpec.from_catalog(record)
        return None

    def _apply_to_targets(
        self,
        *,
        actor: Any,
        target_ids: list[str],
        record: dict | None,
        ctx: dict,
    ) -> list[TargetOutcome]:
        if not target_ids or not record:
            return []
        outcomes: list[TargetOutcome] = []
        attack_kind = (record.get("attack_roll") or "").lower() or None
        save_block = record.get("save") if isinstance(record.get("save"), dict) else None
        damage_block = record.get("damage") if isinstance(record.get("damage"), dict) else None
        effects = record.get("effects") if isinstance(record.get("effects"), list) else []
        half_on_save = bool((save_block or {}).get("half_on_save", False))

        for tid in target_ids:
            target = self._lookup(tid)
            outcome = TargetOutcome(target_id=tid)
            if target is None:
                outcome.note = "target_missing"
                outcomes.append(outcome)
                continue

            applies_damage = True
            applies_effects_on = "always"  # "always" | "fail" | "hit"

            # Attack-roll branch.
            if attack_kind and self.rules is not None and actor is not None:
                atk_mod = int(getattr(actor, "spell_attack_bonus", 0) or 0)
                atk = self.rules.attack(actor, target, attack_modifier=atk_mod)
                outcome.hit = bool(atk.hit)
                outcome.crit = bool(atk.crit)
                applies_damage = atk.hit
                applies_effects_on = "hit"

            # Saving-throw branch (independent of attack roll).
            elif save_block and self.rules is not None:
                ability = str(save_block.get("ability") or "dex").lower()
                dc = int(
                    ctx.get("dc")
                    or save_block.get("dc")
                    or getattr(actor, "spell_save_dc", 0)
                    or 10
                )
                mod = int((getattr(target, "saving_throws", {}) or {}).get(ability, 0))
                check = self.rules.saving_throw(target, modifier=mod, dc=dc)
                outcome.save_success = bool(check.success)
                applies_effects_on = "fail"
                # Damage still rolls; halve on success when configured.

            # Damage roll + apply.
            if applies_damage and damage_block and self.rules is not None:
                total, primary_type = self._roll_damage(
                    target=target,
                    parts=damage_block.get("parts") or [],
                    crit=outcome.crit,
                )
                if (
                    outcome.save_success is True
                    and half_on_save
                    and total > 0
                ):
                    total = total // 2
                outcome.damage = total
                outcome.damage_type = primary_type
                if total > 0:
                    self.rules.apply_damage(
                        target, total,
                        damage_type=primary_type or "untyped",
                        crit=outcome.crit,
                    )
                outcome.hp_after = getattr(target, "hp", None)

            # Effects (conditions on hit/fail/always).
            for entry in effects:
                if not isinstance(entry, dict):
                    continue
                cond = str(entry.get("condition") or "").strip()
                if not cond:
                    continue
                gate = str(entry.get("on") or "always").lower()
                fires = (
                    gate == "always"
                    or (gate == "hit" and outcome.hit is True)
                    or (gate == "fail" and outcome.save_success is False)
                )
                if not fires:
                    continue
                if self.rules is not None:
                    try:
                        self.rules.add_condition(target, cond)
                    except Exception:  # noqa: BLE001
                        pass
                elif hasattr(target, "add_condition"):
                    try:
                        target.add_condition(cond, source=str(record.get("name") or "spell"))
                    except Exception:  # noqa: BLE001
                        pass
                outcome.effects_applied.append(cond)

            if outcome.hp_after is None:
                outcome.hp_after = getattr(target, "hp", None)
            outcomes.append(outcome)

        return outcomes

    def _roll_damage(
        self,
        *,
        target: Any,
        parts: list[Any],
        crit: bool,
    ) -> tuple[int, str | None]:
        """Roll every ``[dice, type]`` part against ``target`` and sum."""
        total = 0
        primary_type: str | None = None
        for part in parts:
            if not isinstance(part, (list, tuple)) or len(part) < 2:
                continue
            dice = str(part[0])
            dmg_type = str(part[1])
            if primary_type is None:
                primary_type = dmg_type
            dmg = self.rules.damage(  # type: ignore[union-attr]
                target, dice=dice, damage_type=dmg_type, crit=crit,
            )
            total += int(dmg.total)
        return total, primary_type

    def _start_concentration(
        self,
        actor: Any,
        *,
        spell_id: Any,
        record: dict | None,
        target_ids: list[str],
    ) -> None:
        try:
            from ai_dm.game.combatant_state import Concentration
            actor.concentration = Concentration(
                spell_id=str(spell_id or "unknown"),
                name=str((record or {}).get("name") or spell_id or "unknown"),
                target_ids=list(target_ids),
            )
        except Exception:  # noqa: BLE001 — duck-typed actor without concentration
            pass

    # ------------------------------------------------------------------ #
    # Action economy / slot helpers
    # ------------------------------------------------------------------ #

    def _consume_economy(self, actor: Any, slot: str) -> bool:
        """Mark the requested slot as spent. ``free`` is always allowed."""
        if actor is None or slot == "free":
            return True
        attr = {
            "action": "action_used",
            "bonus": "bonus_action_used",
            "reaction": "reaction_used",
        }.get(slot)
        if attr is None:
            return True
        if getattr(actor, attr, False):
            return False
        if hasattr(actor, attr):
            setattr(actor, attr, True)
        return True

    def _refund_slot(self, actor: Any, level: int) -> None:
        if level <= 0:
            return
        slots = getattr(actor, "spell_slots", None) or {}
        slot = slots.get(level)
        if slot is None:
            return
        try:
            slot.current = min(int(slot.max), int(slot.current) + 1)
        except Exception:  # noqa: BLE001
            pass

    def _lookup(self, actor_id: str) -> Any:
        if not actor_id or self.actor_lookup is None:
            return None
        try:
            return self.actor_lookup(actor_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("actor lookup failed for %s: %s", actor_id, exc)
            return None


__all__ = [
    "SpellCastResult",
    "SpellResolver",
    "TargetOutcome",
]

