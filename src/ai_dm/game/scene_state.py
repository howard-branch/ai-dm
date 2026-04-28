"""Per-scene runtime state: hazards, traps, light sources, ambient light.

A :class:`SceneState` is the mutable companion to the immutable
:class:`ai_dm.game.location_model.SceneLocation`. It carries everything
the rules engine needs to "tick" the scene — light burning down,
hazards harming whoever stands in them, traps waiting to spring.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_dm.rules import hazards as hz
from ai_dm.rules import light as lt
from ai_dm.rules import traps as tr
from ai_dm.rules.dice import DiceRoller


# --------------------------------------------------------------------- #
# Hazards
# --------------------------------------------------------------------- #


class ActiveHazard(BaseModel):
    """One placed instance of a hazard template in this scene."""

    model_config = ConfigDict(extra="forbid")

    instance_id: str
    hazard_key: str
    zone_id: str | None = None
    last_tick_round: int | None = None
    last_tick_minute: int | None = None
    disabled: bool = False
    notes: str | None = None

    def template(self) -> "hz.Hazard | None":
        return hz.get_hazard(self.hazard_key)

    def tick(
        self,
        actors: list[Any],
        *,
        roller: DiceRoller | None = None,
        now_round: int | None = None,
        now_minute: int | None = None,
    ) -> list[hz.HazardOutcome]:
        if self.disabled:
            return []
        tpl = self.template()
        if tpl is None:
            return []
        outs = [hz.resolve_tick(tpl, a, roller=roller) for a in actors]
        self.last_tick_round = now_round
        self.last_tick_minute = now_minute
        return outs


# --------------------------------------------------------------------- #
# Traps
# --------------------------------------------------------------------- #


class ArmedTrap(BaseModel):
    """One placed instance of a trap template (armed or expended)."""

    model_config = ConfigDict(extra="forbid")

    instance_id: str
    trap_key: str
    zone_id: str | None = None
    anchor_id: str | None = None
    detected_by: list[str] = Field(default_factory=list)
    disarmed: bool = False
    expended: bool = False
    last_triggered_round: int | None = None
    last_triggered_seconds: int | None = None

    def template(self) -> "tr.Trap | None":
        return tr.get_trap(self.trap_key)

    def is_armed(self) -> bool:
        return not self.disarmed and not self.expended

    def mark_detected(self, actor_id: str) -> bool:
        if actor_id not in self.detected_by:
            self.detected_by.append(actor_id)
            return True
        return False

    def disarm(self) -> bool:
        if self.disarmed or self.expended:
            return False
        self.disarmed = True
        return True

    def trigger(
        self,
        actor: Any,
        *,
        roller: DiceRoller | None = None,
        now_round: int | None = None,
        now_seconds: int | None = None,
    ) -> tr.TrapResolution | None:
        tpl = self.template()
        if tpl is None or not self.is_armed():
            return None
        res = tr.resolve_trigger(tpl, actor, roller=roller)
        self.last_triggered_round = now_round
        self.last_triggered_seconds = now_seconds
        if res.expended:
            self.expended = True
        return res

    def reset(self, *, now_seconds: int | None = None) -> bool:
        """Re-arm a manually / auto-resettable trap."""
        tpl = self.template()
        if tpl is None or tpl.reset == "never":
            return False
        if tpl.reset == "auto" and now_seconds is None:
            return False
        self.expended = False
        self.disarmed = False
        return True


# --------------------------------------------------------------------- #
# Light
# --------------------------------------------------------------------- #


class AmbientLight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: lt.VisionLevel = "bright"
    description: str | None = None


class LightSourceInstance(BaseModel):
    """One physical (or magical) light source in the scene."""

    model_config = ConfigDict(extra="forbid")

    instance_id: str
    light_key: str
    lit: bool = True
    carrier_actor_id: str | None = None
    anchor_id: str | None = None
    minutes_remaining: float | None = None  # None → permanent
    inventory_instance_id: str | None = None  # link to a CarriedItem if any

    def template(self) -> "lt.LightSource | None":
        return lt.get_light(self.light_key)

    def light(self) -> bool:
        if self.lit:
            return False
        tpl = self.template()
        if tpl is None:
            return False
        if self.minutes_remaining is None and tpl.duration_min is not None:
            self.minutes_remaining = float(tpl.duration_min)
        self.lit = True
        return True

    def extinguish(self) -> bool:
        was = self.lit
        self.lit = False
        return was

    def tick(self, minutes: float) -> bool:
        """Burn ``minutes``; return ``True`` if it just burned out."""
        if not self.lit or self.minutes_remaining is None:
            return False
        self.minutes_remaining = max(0.0, float(self.minutes_remaining) - float(minutes))
        if self.minutes_remaining <= 0:
            self.lit = False
            return True
        return False


# --------------------------------------------------------------------- #
# Container
# --------------------------------------------------------------------- #


class SceneState(BaseModel):
    """Aggregate runtime state for one scene."""

    model_config = ConfigDict(extra="forbid")

    scene_id: str
    ambient_light: AmbientLight = Field(default_factory=AmbientLight)
    active_hazards: list[ActiveHazard] = Field(default_factory=list)
    armed_traps: list[ArmedTrap] = Field(default_factory=list)
    light_sources: list[LightSourceInstance] = Field(default_factory=list)
    schema_version: int = 1

    # ---- mutators -------------------------------------------------- #

    def place_hazard(
        self,
        hazard_key: str,
        *,
        zone_id: str | None = None,
        instance_id: str | None = None,
    ) -> ActiveHazard:
        if not hz.is_hazard(hazard_key):
            raise ValueError(f"unknown hazard {hazard_key!r}")
        iid = instance_id or self._unique_id(hazard_key, [h.instance_id for h in self.active_hazards])
        inst = ActiveHazard(instance_id=iid, hazard_key=hazard_key, zone_id=zone_id)
        self.active_hazards.append(inst)
        return inst

    def place_trap(
        self,
        trap_key: str,
        *,
        zone_id: str | None = None,
        anchor_id: str | None = None,
        instance_id: str | None = None,
    ) -> ArmedTrap:
        if not tr.is_trap(trap_key):
            raise ValueError(f"unknown trap {trap_key!r}")
        iid = instance_id or self._unique_id(trap_key, [t.instance_id for t in self.armed_traps])
        inst = ArmedTrap(
            instance_id=iid,
            trap_key=trap_key,
            zone_id=zone_id,
            anchor_id=anchor_id,
        )
        self.armed_traps.append(inst)
        return inst

    def add_light(
        self,
        light_key: str,
        *,
        carrier_actor_id: str | None = None,
        anchor_id: str | None = None,
        lit: bool = True,
        inventory_instance_id: str | None = None,
        instance_id: str | None = None,
    ) -> LightSourceInstance:
        tpl = lt.get_light(light_key)
        if tpl is None:
            raise ValueError(f"unknown light source {light_key!r}")
        iid = instance_id or self._unique_id(light_key, [l.instance_id for l in self.light_sources])
        inst = LightSourceInstance(
            instance_id=iid,
            light_key=light_key,
            lit=lit,
            carrier_actor_id=carrier_actor_id,
            anchor_id=anchor_id,
            minutes_remaining=float(tpl.duration_min) if tpl.duration_min is not None else None,
            inventory_instance_id=inventory_instance_id,
        )
        self.light_sources.append(inst)
        return inst

    def tick_lights(self, minutes: float) -> list[str]:
        """Burn every lit light by ``minutes``. Returns ids that burned out."""
        burned: list[str] = []
        for src in self.light_sources:
            if src.tick(minutes):
                burned.append(src.instance_id)
        return burned

    @staticmethod
    def _unique_id(key: str, existing: list[str]) -> str:
        n = 1
        used = set(existing)
        while True:
            cand = f"{key}#{n}"
            if cand not in used:
                return cand
            n += 1


__all__ = [
    "ActiveHazard",
    "AmbientLight",
    "ArmedTrap",
    "LightSourceInstance",
    "SceneState",
]

