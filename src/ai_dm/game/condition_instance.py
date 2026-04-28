"""Machine-actionable D&D 5e condition state.

Conditions used to be stored on :class:`CombatantState` as a flat
``list[str]`` of SRD keys. That worked for membership tests in the
attacker / target advantage maths but lost everything an automated DM
needs to actually *run* the condition: who applied it, when does it
end, is it riding on a concentration spell, can the target try a
save-to-end on its turn, and so on.

This module defines the rich record:

* :class:`SaveToEnd` — periodic save the target rolls to shake the
  condition off (e.g. Hold Person, Charm Person variants).
* :class:`ConcentrationLink` — back-pointer to the caster + spell
  sustaining the effect; used by the concentration cascade so that
  breaking concentration auto-clears every linked condition.
* :class:`ConditionInstance` — one applied condition on one combatant.
  Equality / dedupe key is ``(key, source)`` so multiple casters can
  inflict the *same* SRD condition on a target without overwriting
  each other.

The model is intentionally pure data; lifecycle and cascade logic
lives in :mod:`ai_dm.game.condition_tracker`. SRD catalogue lookup
lives in :mod:`ai_dm.rules.conditions`.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Keep imports of the catalogue lazy to avoid an import cycle with
# ``rules.conditions`` (which itself loads JSON at module import).
def _all_condition_keys() -> tuple[str, ...]:
    from ai_dm.rules.conditions import ALL_CONDITIONS
    return ALL_CONDITIONS


SaveAbility = Literal["str", "dex", "con", "int", "wis", "cha"]
SaveTiming = Literal["start_of_turn", "end_of_turn"]
ExpiryTrigger = Literal[
    "start_of_target_turn",
    "end_of_target_turn",
    "start_of_source_turn",
    "end_of_source_turn",
    "end_of_encounter",
    "never",
]


class SaveToEnd(BaseModel):
    """A repeating save the target may roll to end the condition."""

    model_config = ConfigDict(extra="forbid")

    ability: SaveAbility
    dc: int
    when: SaveTiming = "end_of_turn"
    once_per_turn: bool = True
    advantage: bool = False
    disadvantage: bool = False


class ConcentrationLink(BaseModel):
    """Back-pointer from a condition to the spell sustaining it."""

    model_config = ConfigDict(extra="forbid")

    caster_id: str
    spell_id: str


class ConditionInstance(BaseModel):
    """One SRD condition applied to one combatant.

    ``key`` is validated against the SRD catalogue so callers can't
    accidentally invent conditions; unknown keys are still tolerated
    (logged + accepted as ``custom``) by the tracker, which keeps
    backwards-compatibility with the legacy ``list[str]`` shape.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    source: str = "unknown"
    applied_round: int | None = None
    expires_at_round: int | None = None
    duration_rounds: int | None = None
    expires_on: ExpiryTrigger = "never"
    save_to_end: SaveToEnd | None = None
    concentration_link: ConcentrationLink | None = None
    level: int = 1
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)

    @field_validator("key")
    @classmethod
    def _normalise_key(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("ConditionInstance.key must be a non-empty string")
        return v.strip().lower()

    # ------------------------------------------------------------------ #
    # Dedupe / merge helpers
    # ------------------------------------------------------------------ #

    @property
    def dedupe_key(self) -> tuple[str, str]:
        """Two instances are 'the same' iff key + source match."""
        return (self.key, self.source)

    def supersedes(self, other: "ConditionInstance") -> bool:
        """``self`` should replace ``other`` on re-apply.

        Wins on either a later explicit ``expires_at_round`` or a longer
        ``duration_rounds``. A permanent (no-expiry) instance always
        beats a timed one.
        """
        if other.expires_on == "never" and other.expires_at_round is None \
                and other.duration_rounds is None:
            # other is permanent
            return False
        if self.expires_on == "never" and self.expires_at_round is None \
                and self.duration_rounds is None:
            return True
        s_end = self.expires_at_round if self.expires_at_round is not None else -1
        o_end = other.expires_at_round if other.expires_at_round is not None else -1
        if s_end != o_end:
            return s_end > o_end
        s_dur = self.duration_rounds or 0
        o_dur = other.duration_rounds or 0
        return s_dur >= o_dur


# --------------------------------------------------------------------- #
# Mixed-list helpers — used by callers that still see legacy list[str]
# --------------------------------------------------------------------- #


def coerce_instance(value: Any, *, default_source: str = "legacy") -> ConditionInstance | None:
    """Best-effort promote ``value`` to a :class:`ConditionInstance`.

    Accepts:
    * an existing :class:`ConditionInstance` (returned unchanged),
    * a bare string SRD key (legacy on-disk shape),
    * a dict matching the model schema.

    Returns ``None`` when ``value`` cannot be promoted (caller decides
    whether to drop or warn).
    """
    if isinstance(value, ConditionInstance):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        return ConditionInstance(key=s, source=default_source)
    if isinstance(value, dict):
        try:
            return ConditionInstance.model_validate(value)
        except Exception:  # noqa: BLE001
            return None
    return None


def key_of(value: Any) -> str | None:
    """Extract the SRD key from ``value`` (str, ConditionInstance, dict)."""
    if isinstance(value, str):
        s = value.strip().lower()
        return s or None
    if isinstance(value, ConditionInstance):
        return value.key
    if isinstance(value, dict):
        k = value.get("key")
        if isinstance(k, str) and k.strip():
            return k.strip().lower()
    return None


__all__ = [
    "ConcentrationLink",
    "ConditionInstance",
    "ExpiryTrigger",
    "SaveAbility",
    "SaveTiming",
    "SaveToEnd",
    "coerce_instance",
    "key_of",
]

