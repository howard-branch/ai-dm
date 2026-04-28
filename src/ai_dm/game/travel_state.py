"""Party-wide travel / exploration state.

Persists pace, terrain, mounts, hours marched today, total miles, and
the running forced-march save tally. Stays focused on overland travel;
intra-scene movement is handled by :mod:`ai_dm.rules.movement`.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_dm.rules import sustenance, travel
from ai_dm.rules.dice import DiceRoller


class ForcedMarchOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str
    hours_marched: int
    dc: int
    save_total: int
    succeeded: bool
    exhaustion_after: int


class TravelTickResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    miles_added: float = 0.0
    hours_added: float = 0.0
    forced_march_triggered: bool = False
    forced_march_outcomes: list[ForcedMarchOutcome] = Field(default_factory=list)
    starvation_exhaustion: dict[str, int] = Field(default_factory=dict)


class TravelState(BaseModel):
    """Mutable travel slice for one party.

    Distance and time accumulate via :meth:`advance`. Per-actor effects
    (forced-march exhaustion, food/water debt) are applied in-place on
    the ``CombatantState`` instances passed in; the return value
    summarises what happened for narration.
    """

    model_config = ConfigDict(extra="forbid")

    party_id: str | None = None
    pace: travel.Pace = "normal"
    mounted: bool = False
    mount_keys: dict[str, str] = Field(default_factory=dict)
    terrain: str = "normal"
    hours_today: float = 0.0
    days_elapsed: int = 0
    total_miles: float = 0.0
    navigators: list[str] = Field(default_factory=list)
    lost: bool = False
    last_navigation_dc: int | None = None
    forced_march_hours: int = 0
    last_tick: TravelTickResult | None = None
    schema_version: int = 1

    # ---- mutators -------------------------------------------------- #

    def set_pace(self, p: travel.Pace) -> None:
        self.pace = p

    def set_terrain(self, terrain: str) -> None:
        self.terrain = terrain

    def start_new_day(self) -> None:
        self.hours_today = 0.0
        self.days_elapsed += 1
        self.forced_march_hours = 0

    def advance(
        self,
        hours: float,
        *,
        actors: list[Any] | None = None,
        roller: DiceRoller | None = None,
    ) -> TravelTickResult:
        """Advance the party by ``hours``.

        * Adds miles based on pace + terrain + mount state.
        * If cumulative day hours exceed the forced-march threshold,
          rolls Con saves for each actor and tracks exhaustion.
        """
        miles = travel.distance_per_hour_mi(
            self.pace, mounted=self.mounted, terrain=self.terrain
        ) * float(hours)
        self.total_miles = round(self.total_miles + miles, 4)
        self.hours_today += float(hours)

        result = TravelTickResult(miles_added=miles, hours_added=float(hours))

        # Forced march: any whole hour past the threshold this day.
        threshold = travel.FORCED_MARCH.hours_before_check
        whole_hours_today = int(self.hours_today)
        new_extra = max(0, whole_hours_today - max(self.forced_march_hours, threshold))
        if whole_hours_today > threshold and new_extra > 0 and actors:
            result.forced_march_triggered = True
            r = roller or DiceRoller(seed=0)
            for hr_idx in range(1, new_extra + 1):
                hours_marched_now = max(self.forced_march_hours, threshold) + hr_idx
                dc = travel.forced_march_save_dc(hours_marched_now) or 0
                for a in actors:
                    saves = getattr(a, "saving_throws", {}) or {}
                    mod = int(saves.get(travel.FORCED_MARCH.save_ability, 0))
                    total = r.roll("1d20").total + mod
                    succeeded = total >= dc
                    if not succeeded and hasattr(a, "exhaustion"):
                        a.exhaustion = min(6, int(a.exhaustion) + travel.FORCED_MARCH.exhaustion_on_fail)
                    result.forced_march_outcomes.append(
                        ForcedMarchOutcome(
                            actor_id=str(getattr(a, "actor_id", "")),
                            hours_marched=hours_marched_now,
                            dc=dc,
                            save_total=total,
                            succeeded=succeeded,
                            exhaustion_after=int(getattr(a, "exhaustion", 0) or 0),
                        )
                    )
            self.forced_march_hours = whole_hours_today

        self.last_tick = result
        return result

    def make_navigation_check(
        self,
        base_dc: int,
        *,
        navigator_total: int,
    ) -> bool:
        dc = travel.navigation_dc(base_dc, self.pace)
        self.last_navigation_dc = dc
        success = int(navigator_total) >= dc
        self.lost = not success
        return success

    # ---- food/water sweep ----------------------------------------- #

    def daily_subsistence_check(
        self,
        actors: list[Any],
        *,
        days_without_food: dict[str, int] | None = None,
        days_without_water: dict[str, int] | None = None,
        prev_dehydration_failures: dict[str, int] | None = None,
        roller: DiceRoller | None = None,
        hot_climate: bool = False,
    ) -> dict[str, int]:
        """Apply once-per-day starvation + dehydration ticks to ``actors``.

        Returns ``{actor_id: new_exhaustion_level}``.
        """
        out: dict[str, int] = {}
        days_without_food = days_without_food or {}
        days_without_water = days_without_water or {}
        prev_dehydration_failures = prev_dehydration_failures or {}
        for a in actors:
            aid = str(getattr(a, "actor_id", ""))
            sustenance.tick_starvation(a, days_without_food=days_without_food.get(aid, 0))
            if days_without_water.get(aid, 0) > 0:
                sustenance.tick_dehydration(
                    a,
                    prev_failed_days=prev_dehydration_failures.get(aid, 0),
                    roller=roller,
                )
            out[aid] = int(getattr(a, "exhaustion", 0) or 0)
        return out


__all__ = ["ForcedMarchOutcome", "TravelState", "TravelTickResult"]

