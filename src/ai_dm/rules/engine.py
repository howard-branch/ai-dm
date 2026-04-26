"""Top-level rules facade.

Combines dice, conditions, house rules, and 5e reference data into a single
service. Combat / action-resolver / planner all consume this, so unit tests
live close to each component but ``test_rules_engine.py`` exercises the
facade end-to-end.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ai_dm.orchestration.event_bus import EventBus
from ai_dm.rules.attack import (
    AttackResult,
    DamageResult,
    apply_resistance,
    make_attack,
    roll_damage,
)
from ai_dm.rules.conditions import (
    ALL_CONDITIONS,
    AttackModifier,
    attacker_mod,
    merge_advantage,
    target_mod,
)
from ai_dm.rules.damage import (
    DamageOutcome,
    apply_damage as _apply_damage,
    apply_healing as _apply_healing,
    grant_temp_hp as _grant_temp_hp,
)
from ai_dm.rules.death_saves import (
    DeathSaveResult,
    DeathSaveTrack,
    damage_at_zero as _ds_damage_at_zero,
    is_massive_damage,
    roll_death_save as _roll_death_save,
)
from ai_dm.rules.dice import DiceRoller, RollResult
from ai_dm.rules.exhaustion import (
    add as _exh_add,
    d20_penalty as _exh_d20_penalty,
    is_dead as _exh_is_dead,
    remove as _exh_remove,
)
from ai_dm.rules.house_rules import HouseRule, HouseRuleSet, load_house_rules
from ai_dm.rules.skill_checks import CheckResult, make_check

logger = logging.getLogger("ai_dm.rules")


@dataclass
class ActorRuleState:
    """Mechanical snapshot of an actor used by the rules engine.

    The ``CombatMachine`` keeps :class:`Participant` objects; this is a
    superset (extra fields are optional) so callers can pass either.
    """

    actor_id: str
    name: str = ""
    hp: int = 0
    max_hp: int = 0
    temp_hp: int = 0
    ac: int = 10
    conditions: list[str] = None  # type: ignore[assignment]
    resistances: list[str] = None  # type: ignore[assignment]
    vulnerabilities: list[str] = None  # type: ignore[assignment]
    immunities: list[str] = None  # type: ignore[assignment]
    exhaustion: int = 0
    death_saves: DeathSaveTrack | None = None

    def __post_init__(self) -> None:
        if self.conditions is None:
            self.conditions = []
        if self.resistances is None:
            self.resistances = []
        if self.vulnerabilities is None:
            self.vulnerabilities = []
        if self.immunities is None:
            self.immunities = []
        if self.death_saves is None:
            self.death_saves = DeathSaveTrack()


class RulesEngine:
    """Sole mechanical authority for checks, attacks, damage, and conditions."""

    def __init__(
        self,
        *,
        house_rules: HouseRuleSet | None = None,
        rng: random.Random | None = None,
        event_bus: EventBus | None = None,
        seed: int | None = None,
    ) -> None:
        self.house_rules = house_rules or HouseRuleSet()
        self.event_bus = event_bus
        self.roller = DiceRoller(rng=rng, seed=seed)

    # ------------------------------------------------------------------ #
    # Construction helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def from_assets(
        cls,
        assets_dir: Path,
        *,
        rng: random.Random | None = None,
        event_bus: EventBus | None = None,
    ) -> "RulesEngine":
        house = load_house_rules(assets_dir / "house_rules.json")
        return cls(house_rules=house, rng=rng, event_bus=event_bus)

    # ------------------------------------------------------------------ #
    # Dice
    # ------------------------------------------------------------------ #

    def roll(self, expression: str, *, advantage: str = "normal") -> RollResult:
        result = self.roller.roll(expression, advantage=advantage)  # type: ignore[arg-type]
        self._publish("rules.roll_made", {"expression": expression, "result": result.to_dict()})
        return result

    # ------------------------------------------------------------------ #
    # Checks
    # ------------------------------------------------------------------ #

    def ability_check(
        self,
        actor: ActorRuleState,
        *,
        modifier: int,
        dc: int,
        advantage: str | None = None,
    ) -> CheckResult:
        adv = self._advantage_for(actor, target=None, override=advantage)
        eff_mod = int(modifier) + _exh_d20_penalty(getattr(actor, "exhaustion", 0))
        result = make_check(self.roller, modifier=eff_mod, dc=dc, advantage=adv)
        self._publish(
            "rules.check_resolved",
            {"actor_id": actor.actor_id, "dc": dc, "result": result.to_dict()},
        )
        return result

    def saving_throw(
        self,
        actor: ActorRuleState,
        *,
        modifier: int,
        dc: int,
        advantage: str | None = None,
    ) -> CheckResult:
        # Mechanically identical to an ability check in 5e.
        return self.ability_check(actor, modifier=modifier, dc=dc, advantage=advantage)

    # ------------------------------------------------------------------ #
    # Attacks & damage
    # ------------------------------------------------------------------ #

    def attack(
        self,
        attacker: ActorRuleState,
        target: ActorRuleState,
        *,
        attack_modifier: int,
        advantage: str | None = None,
    ) -> AttackResult:
        adv = self._advantage_for(attacker, target=target, override=advantage)
        eff_mod = int(attack_modifier) + _exh_d20_penalty(
            getattr(attacker, "exhaustion", 0)
        )
        result = make_attack(
            self.roller,
            attack_modifier=eff_mod,
            target_ac=target.ac,
            advantage=adv,
        )
        self._publish(
            "rules.attack_resolved",
            {
                "attacker_id": attacker.actor_id,
                "target_id": target.actor_id,
                "result": result.to_dict(),
            },
        )
        return result

    def damage(
        self,
        target: ActorRuleState,
        *,
        dice: str,
        bonus: int = 0,
        damage_type: str = "untyped",
        crit: bool = False,
    ) -> DamageResult:
        crit_doubles = bool(
            self.house_rules.get("damage", "crit_doubles_dice", True)
        )
        rolled = roll_damage(
            self.roller,
            dice=dice,
            bonus=bonus,
            damage_type=damage_type,
            crit=crit,
            crit_doubles_dice=crit_doubles,
        )
        rolled.total = apply_resistance(
            rolled.total,
            damage_type,
            resistances=target.resistances,
            vulnerabilities=target.vulnerabilities,
            immunities=target.immunities,
        )
        return rolled

    def apply_damage(
        self,
        target: ActorRuleState,
        amount: int,
        *,
        damage_type: str = "untyped",
        crit: bool = False,
    ) -> int:
        """Apply ``amount`` damage to ``target``. Returns new HP.

        * Soaks ``temp_hp`` first.
        * If the target was already at 0 HP, registers a death-save
          failure (two on a critical hit) and checks massive damage.
        * If the hit drops the target to 0 HP, applies the unconscious
          condition and checks massive damage (instant death).
        """
        if amount <= 0:
            return target.hp
        was_at_zero = target.hp == 0
        outcome = _apply_damage(target, int(amount), damage_type=damage_type)
        self._publish(
            "rules.damage_applied",
            {
                "target_id": target.actor_id,
                "amount": int(amount),
                "damage_type": damage_type,
                "outcome": outcome.to_dict(),
            },
        )
        if was_at_zero:
            _ds_damage_at_zero(target.death_saves, crit=bool(crit))  # type: ignore[arg-type]
            if is_massive_damage(int(amount), target.max_hp):
                target.death_saves.dead = True  # type: ignore[union-attr]
        elif outcome.dropped_to_zero:
            if "unconscious" not in target.conditions:
                self.add_condition(target, "unconscious")
            if is_massive_damage(outcome.dealt, target.max_hp):
                target.death_saves.dead = True  # type: ignore[union-attr]
        return target.hp

    def heal(self, target: ActorRuleState, amount: int) -> int:
        if amount <= 0:
            return target.hp
        was_at_zero = target.hp == 0
        new_hp = _apply_healing(target, int(amount))
        if new_hp > 0 and "unconscious" in target.conditions:
            self.remove_condition(target, "unconscious")
        if was_at_zero and new_hp > 0 and target.death_saves is not None:
            target.death_saves.reset()
        return new_hp

    def grant_temp_hp(self, target: ActorRuleState, amount: int) -> int:
        return _grant_temp_hp(target, int(amount))

    # ------------------------------------------------------------------ #
    # Death saves & exhaustion
    # ------------------------------------------------------------------ #

    def death_save(self, actor: ActorRuleState) -> DeathSaveResult:
        if actor.death_saves is None:
            actor.death_saves = DeathSaveTrack()
        result = _roll_death_save(actor.death_saves, self.roller)
        if result.healed_to is not None:
            actor.hp = max(actor.hp, int(result.healed_to))
            if "unconscious" in actor.conditions:
                self.remove_condition(actor, "unconscious")
        self._publish(
            "rules.death_save",
            {"actor_id": actor.actor_id, "result": result.to_dict()},
        )
        return result

    def add_exhaustion(self, actor: ActorRuleState, n: int = 1) -> int:
        actor.exhaustion = _exh_add(actor.exhaustion, n)
        if _exh_is_dead(actor.exhaustion):
            actor.hp = 0
            if actor.death_saves is not None:
                actor.death_saves.dead = True
        self._publish(
            "rules.exhaustion_changed",
            {"actor_id": actor.actor_id, "level": actor.exhaustion},
        )
        return actor.exhaustion

    def remove_exhaustion(self, actor: ActorRuleState, n: int = 1) -> int:
        actor.exhaustion = _exh_remove(actor.exhaustion, n)
        self._publish(
            "rules.exhaustion_changed",
            {"actor_id": actor.actor_id, "level": actor.exhaustion},
        )
        return actor.exhaustion

    # ------------------------------------------------------------------ #
    # Conditions
    # ------------------------------------------------------------------ #

    def add_condition(self, actor: ActorRuleState, condition: str) -> None:
        if condition not in ALL_CONDITIONS:
            logger.warning("unknown condition %r — accepting as custom tag", condition)
        if condition not in actor.conditions:
            actor.conditions.append(condition)
            self._publish(
                "rules.condition_added",
                {"actor_id": actor.actor_id, "condition": condition},
            )

    def remove_condition(self, actor: ActorRuleState, condition: str) -> None:
        if condition in actor.conditions:
            actor.conditions.remove(condition)
            self._publish(
                "rules.condition_removed",
                {"actor_id": actor.actor_id, "condition": condition},
            )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _advantage_for(
        self,
        actor: ActorRuleState,
        *,
        target: ActorRuleState | None,
        override: str | None,
    ) -> str:
        if override in ("advantage", "disadvantage", "normal"):
            return override
        mods: list[AttackModifier] = [attacker_mod(actor.conditions)]
        if target is not None:
            mods.append(target_mod(target.conditions))
        return merge_advantage(*mods)

    def _publish(self, event: str, payload: dict) -> None:
        if self.event_bus is None:
            return
        try:
            self.event_bus.publish(event, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("event publish failed for %s: %s", event, exc)


__all__ = [
    "ActorRuleState",
    "RulesEngine",
    "DeathSaveResult",
    "DeathSaveTrack",
    "DamageOutcome",
    "HouseRule",
    "HouseRuleSet",
]

