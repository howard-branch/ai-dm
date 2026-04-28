"""Lifecycle and cascade engine for :class:`ConditionInstance`s.

The tracker is the single place that knows *how* conditions enter and
leave a combatant's state. It is intentionally thin — the data lives
on :class:`ai_dm.game.combatant_state.CombatantState`, and the SRD
catalogue lookups live in :mod:`ai_dm.rules.conditions` — but it
owns:

* condition-immunity gating on apply,
* SRD implication expansion (paralyzed → incapacitated, unconscious
  → prone + incapacitated, etc.) with provenance tracking so a later
  remove only takes back the implications *this* parent introduced,
* dedupe-by-(key, source) on apply with longest-duration wins,
* per-tick lifecycle (:meth:`tick_start_of_turn`,
  :meth:`tick_end_of_turn`, :meth:`tick_start_of_round`,
  :meth:`tick_end_encounter`) including expiry-by-round and
  save-to-end rolls,
* concentration / death cascades that drop linked conditions on every
  participant when a caster's concentration breaks or the caster dies.

The tracker is constructed *per call site* — there is no global
state. For cross-actor cascades, callers supply an ``actors`` iterable
(typically ``CombatState.participants``).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Iterable

from ai_dm.game.condition_instance import ConcentrationLink, ConditionInstance, SaveToEnd
from ai_dm.rules.conditions import ALL_CONDITIONS, _EFFECTS

if TYPE_CHECKING:  # pragma: no cover
    from ai_dm.game.combatant_state import CombatantState
    from ai_dm.rules.dice import DiceRoller

logger = logging.getLogger("ai_dm.conditions")

# Implication map: parent SRD effect-key → SRD condition key it implies.
_IMPLICATIONS: tuple[tuple[str, str], ...] = (
    ("incapacitated_implied", "incapacitated"),
    ("prone_implied", "prone"),
)

# Marker we stamp into ``source`` when an implication is auto-added.
_IMPLIED_BY = "implied_by:"


class ConditionTracker:
    """Operate on a single combatant's condition list."""

    def __init__(self, combatant: "CombatantState") -> None:
        self.combatant = combatant

    # ------------------------------------------------------------------ #
    # Apply / remove
    # ------------------------------------------------------------------ #

    def apply(self, instance: ConditionInstance) -> ConditionInstance | None:
        """Add ``instance`` (or merge with an existing same-source copy).

        Returns the resulting instance, or ``None`` when the combatant
        is immune to the SRD condition (a no-op).
        """
        c = self.combatant
        if c.has_condition_immunity(instance.key):
            logger.info(
                "condition %s suppressed by immunity on actor=%s",
                instance.key, c.actor_id,
            )
            return None
        if instance.key not in ALL_CONDITIONS:
            logger.warning(
                "applying non-SRD condition %r to actor=%s (kept as custom tag)",
                instance.key, c.actor_id,
            )
        # Dedupe by (key, source); longer wins on re-apply.
        for existing in list(c.conditions):
            if existing.dedupe_key == instance.dedupe_key:
                if instance.supersedes(existing):
                    c.conditions.remove(existing)
                    c.conditions.append(instance)
                    self._add_implications(instance)
                    return instance
                return existing
        c.conditions.append(instance)
        self._add_implications(instance)
        return instance

    def remove(self, key: str, *, source: str | None = None) -> int:
        """Remove instance(s) of ``key``.

        * ``source=None`` → drop *all* instances of ``key`` regardless
          of who applied them.
        * ``source=<str>`` → drop only the matching ``(key, source)``
          pair (so two casters' Hold Person stay independent).

        Returns the number of instances removed.
        """
        c = self.combatant
        keep: list[ConditionInstance] = []
        removed: list[ConditionInstance] = []
        for inst in c.conditions:
            if inst.key == key and (source is None or inst.source == source):
                removed.append(inst)
            else:
                keep.append(inst)
        c.conditions = keep
        # Take back any implications this instance introduced.
        for inst in removed:
            self._remove_implications(inst)
        return len(removed)

    # ------------------------------------------------------------------ #
    # Lifecycle hooks
    # ------------------------------------------------------------------ #

    def tick_start_of_turn(self, *, current_round: int,
                           roller: "DiceRoller | None" = None,
                           saves: dict[str, int] | None = None) -> list[ConditionInstance]:
        """Called when this combatant starts its turn.

        Drops anything tagged ``expires_on="start_of_target_turn"`` or
        whose ``expires_at_round`` has come due. Rolls any
        ``save_to_end`` configured for ``when="start_of_turn"``.
        Returns the list of instances that expired.
        """
        return self._tick(
            current_round=current_round,
            trigger="start_of_target_turn",
            save_when="start_of_turn",
            roller=roller,
            saves=saves,
        )

    def tick_end_of_turn(self, *, current_round: int,
                         roller: "DiceRoller | None" = None,
                         saves: dict[str, int] | None = None) -> list[ConditionInstance]:
        return self._tick(
            current_round=current_round,
            trigger="end_of_target_turn",
            save_when="end_of_turn",
            roller=roller,
            saves=saves,
        )

    def tick_start_of_round(self, *, current_round: int) -> list[ConditionInstance]:
        """Round-level expiry only (no per-target save-to-end here)."""
        return self._tick(
            current_round=current_round,
            trigger=None,
            save_when=None,
            roller=None,
            saves=None,
        )

    def tick_end_encounter(self) -> list[ConditionInstance]:
        c = self.combatant
        expired = [i for i in c.conditions if i.expires_on == "end_of_encounter"]
        for inst in expired:
            self.remove(inst.key, source=inst.source)
        return expired

    # ------------------------------------------------------------------ #
    # Cascades
    # ------------------------------------------------------------------ #

    def drop_concentration_links(self, caster_id: str) -> list[ConditionInstance]:
        """Drop every condition tied to ``caster_id``'s concentration."""
        c = self.combatant
        dropped: list[ConditionInstance] = []
        for inst in list(c.conditions):
            link = inst.concentration_link
            if link is not None and link.caster_id == caster_id:
                self.remove(inst.key, source=inst.source)
                dropped.append(inst)
        return dropped

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _tick(self, *, current_round: int, trigger: str | None,
              save_when: str | None, roller, saves) -> list[ConditionInstance]:
        c = self.combatant
        expired: list[ConditionInstance] = []
        # 1) Save-to-end (may drop the instance before duration check).
        if save_when is not None:
            for inst in list(c.conditions):
                stx = inst.save_to_end
                if stx is None or stx.when != save_when:
                    continue
                if self._roll_save_to_end(stx, roller=roller, saves=saves):
                    self.remove(inst.key, source=inst.source)
                    expired.append(inst)
        # 2) Trigger / round-based expiry.
        for inst in list(c.conditions):
            if trigger is not None and inst.expires_on == trigger:
                self.remove(inst.key, source=inst.source)
                expired.append(inst)
                continue
            if inst.expires_at_round is not None and current_round >= inst.expires_at_round:
                self.remove(inst.key, source=inst.source)
                expired.append(inst)
        return expired

    @staticmethod
    def _roll_save_to_end(save: SaveToEnd, *, roller, saves) -> bool:
        """Roll ``save`` and return True if it succeeds (=> drop the condition)."""
        if roller is None or saves is None:
            return False  # caller didn't wire the dice; leave it active
        bonus = int(saves.get(save.ability, 0))
        adv = bool(save.advantage and not save.disadvantage)
        dis = bool(save.disadvantage and not save.advantage)
        result = roller.d20(bonus=bonus, advantage=adv, disadvantage=dis)
        total = getattr(result, "total", None) or getattr(result, "value", None)
        if total is None:
            # Fall back: assume failure rather than crash.
            return False
        return int(total) >= int(save.dc)

    def _add_implications(self, parent: ConditionInstance) -> None:
        c = self.combatant
        eff = _EFFECTS.get(parent.key, {})
        for flag, target_key in _IMPLICATIONS:
            if not eff.get(flag):
                continue
            if c.has_condition_immunity(target_key):
                continue
            tag_source = f"{_IMPLIED_BY}{parent.key}#{parent.id}"
            # Avoid duplicate implication if already present from this parent.
            if any(i.key == target_key and i.source == tag_source for i in c.conditions):
                continue
            c.conditions.append(
                ConditionInstance(
                    key=target_key,
                    source=tag_source,
                    expires_on=parent.expires_on,
                    expires_at_round=parent.expires_at_round,
                    duration_rounds=parent.duration_rounds,
                    concentration_link=parent.concentration_link,
                )
            )

    def _remove_implications(self, parent: ConditionInstance) -> None:
        tag_source = f"{_IMPLIED_BY}{parent.key}#{parent.id}"
        self.combatant.conditions = [
            i for i in self.combatant.conditions if i.source != tag_source
        ]


# --------------------------------------------------------------------- #
# Cross-actor cascades (operate on an iterable of combatants)
# --------------------------------------------------------------------- #


def cascade_concentration_dropped(caster_id: str,
                                  actors: Iterable["CombatantState"]) -> int:
    """Drop linked conditions on every combatant. Returns total dropped."""
    total = 0
    for a in actors:
        total += len(ConditionTracker(a).drop_concentration_links(caster_id))
    return total


def cascade_actor_died(dead_actor_id: str,
                       actors: Iterable["CombatantState"]) -> int:
    """A dead caster automatically loses concentration; cascade follows."""
    return cascade_concentration_dropped(dead_actor_id, actors)


__all__ = [
    "ConcentrationLink",
    "ConditionTracker",
    "cascade_actor_died",
    "cascade_concentration_dropped",
]

