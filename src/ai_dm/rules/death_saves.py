"""Death-save state machine.

SRD 5.2: a creature at 0 HP rolls a d20 at the start of each of its
turns:

* ``>= 10`` → 1 success
* ``< 10``  → 1 failure
* nat 20    → regain 1 HP (and end death-save tracking)
* nat 1     → 2 failures
* 3 successes → stable (no longer rolls)
* 3 failures → dead

Damage taken while at 0 HP counts as 1 failure (2 if it was a critical
hit). If the damage equals or exceeds ``max_hp`` while at 0, the
creature is killed outright (massive damage).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ai_dm.rules.srd_core import load

_DATA = load("death_saves")
DC: int = int(_DATA["dc"])
SUCCESSES_TO_STABLE: int = int(_DATA["successes_to_stable"])
FAILURES_TO_DIE: int = int(_DATA["failures_to_die"])
NAT20_HEALS_TO: int = int(_DATA["nat20_heals_to"])
NAT1_FAILURES: int = int(_DATA["nat1_failures"])
DAMAGE_AT_ZERO_FAILURES: int = int(_DATA["damage_at_zero_failures"])
CRIT_AT_ZERO_FAILURES: int = int(_DATA["crit_at_zero_failures"])
MASSIVE_DAMAGE_FACTOR: int = int(_DATA["massive_damage_threshold_factor"])


@dataclass
class DeathSaveTrack:
    successes: int = 0
    failures: int = 0
    stable: bool = False
    dead: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DeathSaveTrack":
        if not data:
            return cls()
        return cls(
            successes=int(data.get("successes", 0)),
            failures=int(data.get("failures", 0)),
            stable=bool(data.get("stable", False)),
            dead=bool(data.get("dead", False)),
        )

    def reset(self) -> None:
        self.successes = 0
        self.failures = 0
        self.stable = False
        self.dead = False


@dataclass
class DeathSaveResult:
    roll: int
    success: bool
    crit: bool        # nat 20 → regains 1 HP
    fumble: bool      # nat 1  → +2 failures
    track: DeathSaveTrack
    healed_to: int | None = None
    became_stable: bool = False
    died: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "roll": self.roll,
            "success": self.success,
            "crit": self.crit,
            "fumble": self.fumble,
            "track": self.track.to_dict(),
            "healed_to": self.healed_to,
            "became_stable": self.became_stable,
            "died": self.died,
        }


def roll_death_save(track: DeathSaveTrack, roller: Any) -> DeathSaveResult:
    """Roll one death save and mutate ``track`` accordingly.

    ``roller`` is any object exposing ``.roll("1d20")`` returning an
    object with a ``.kept`` list (matching :class:`DiceRoller.RollResult`).
    """
    rr = roller.roll("1d20")
    nat = int(rr.kept[0])
    crit = nat == 20
    fumble = nat == 1
    healed_to: int | None = None
    became_stable = False
    died = False

    if crit:
        # Nat 20 ends death-save tracking and revives at 1 HP.
        track.reset()
        healed_to = NAT20_HEALS_TO
        return DeathSaveResult(nat, True, True, False, track, healed_to)

    if fumble:
        track.failures = min(FAILURES_TO_DIE, track.failures + NAT1_FAILURES)
    elif nat >= DC:
        track.successes = min(SUCCESSES_TO_STABLE, track.successes + 1)
    else:
        track.failures = min(FAILURES_TO_DIE, track.failures + 1)

    if track.failures >= FAILURES_TO_DIE:
        track.dead = True
        died = True
    elif track.successes >= SUCCESSES_TO_STABLE:
        track.stable = True
        became_stable = True

    return DeathSaveResult(
        roll=nat,
        success=nat >= DC and not fumble,
        crit=False,
        fumble=fumble,
        track=track,
        healed_to=healed_to,
        became_stable=became_stable,
        died=died,
    )


def damage_at_zero(track: DeathSaveTrack, *, crit: bool = False) -> DeathSaveTrack:
    """Apply the +1 (or +2 on crit) failure when damaged at 0 HP."""
    add = CRIT_AT_ZERO_FAILURES if crit else DAMAGE_AT_ZERO_FAILURES
    track.failures = min(FAILURES_TO_DIE, track.failures + add)
    track.stable = False
    if track.failures >= FAILURES_TO_DIE:
        track.dead = True
    return track


def is_massive_damage(amount: int, max_hp: int) -> bool:
    """SRD: damage ≥ ``max_hp * factor`` past 0 → instant death."""
    if max_hp <= 0:
        return False
    return int(amount) >= int(max_hp) * MASSIVE_DAMAGE_FACTOR


__all__ = [
    "CRIT_AT_ZERO_FAILURES",
    "DAMAGE_AT_ZERO_FAILURES",
    "DC",
    "DeathSaveResult",
    "DeathSaveTrack",
    "FAILURES_TO_DIE",
    "MASSIVE_DAMAGE_FACTOR",
    "NAT1_FAILURES",
    "NAT20_HEALS_TO",
    "SUCCESSES_TO_STABLE",
    "damage_at_zero",
    "is_massive_damage",
    "roll_death_save",
]

