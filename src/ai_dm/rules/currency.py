"""SRD 5.2.1 currency — coin denominations, conversion and weight.

Single source of truth: ``assets/srd5_2/core/currency.json``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Mapping

from ai_dm.rules.srd_core import load

_DATA = load("currency")

CoinKey = Literal["cp", "sp", "ep", "gp", "pp"]
COIN_KEYS: tuple[CoinKey, ...] = ("cp", "sp", "ep", "gp", "pp")  # type: ignore[assignment]
GP_VALUE: dict[str, float] = {c["key"]: float(c["gp_value"]) for c in _DATA["coins"]}
COINS_PER_POUND: int = int(_DATA["coins_per_pound"])


class InsufficientFunds(ValueError):
    """Raised by :func:`subtract` when there's not enough total wealth."""


@dataclass(frozen=True)
class Coins:
    cp: int = 0
    sp: int = 0
    ep: int = 0
    gp: int = 0
    pp: int = 0

    @classmethod
    def from_mapping(cls, m: Mapping[str, int] | None) -> "Coins":
        if not m:
            return cls()
        return cls(**{k: int(m.get(k, 0)) for k in COIN_KEYS})

    def as_dict(self) -> dict[str, int]:
        return {k: int(getattr(self, k)) for k in COIN_KEYS}

    def total_count(self) -> int:
        return sum(int(getattr(self, k)) for k in COIN_KEYS)


def total_gp(coins: Coins | Mapping[str, int]) -> float:
    """Convert a coin purse to its gp-equivalent value (float)."""
    if isinstance(coins, Coins):
        d = coins.as_dict()
    else:
        d = {k: int(coins.get(k, 0)) for k in COIN_KEYS}
    return round(sum(d[k] * GP_VALUE[k] for k in COIN_KEYS), 4)


def weight(coins: Coins | Mapping[str, int]) -> float:
    """Coins weigh 1 lb per :data:`COINS_PER_POUND` regardless of denomination."""
    n = coins.total_count() if isinstance(coins, Coins) else sum(int(coins.get(k, 0)) for k in COIN_KEYS)
    return round(n / COINS_PER_POUND, 4)


def add(*purses: Coins | Mapping[str, int]) -> Coins:
    out = {k: 0 for k in COIN_KEYS}
    for p in purses:
        d = p.as_dict() if isinstance(p, Coins) else {k: int(p.get(k, 0)) for k in COIN_KEYS}
        for k in COIN_KEYS:
            out[k] += d[k]
    return Coins(**out)


def subtract(have: Coins | Mapping[str, int], cost: Coins | Mapping[str, int]) -> Coins:
    """Pay ``cost`` from ``have`` using greedy down-conversion (pp→gp→ep→sp→cp).

    Raises :class:`InsufficientFunds` if total_gp(have) < total_gp(cost).
    Returns the new purse. Excess change is returned in the smallest
    denominations practical (cp).
    """
    have_d = have.as_dict() if isinstance(have, Coins) else {k: int(have.get(k, 0)) for k in COIN_KEYS}
    cost_d = cost.as_dict() if isinstance(cost, Coins) else {k: int(cost.get(k, 0)) for k in COIN_KEYS}
    if total_gp(have_d) + 1e-9 < total_gp(cost_d):
        raise InsufficientFunds(
            f"need {total_gp(cost_d):.2f} gp, have {total_gp(have_d):.2f} gp"
        )
    # Convert everything to copper, subtract, then return as cp purse.
    have_cp = sum(int(round(have_d[k] * GP_VALUE[k] * 100)) for k in COIN_KEYS)
    cost_cp = sum(int(round(cost_d[k] * GP_VALUE[k] * 100)) for k in COIN_KEYS)
    remaining_cp = have_cp - cost_cp
    # Re-mint in pp/gp/ep/sp/cp greedily so the player keeps recognisable coins.
    pp, r = divmod(remaining_cp, 1000)
    gp, r = divmod(r, 100)
    ep, r = divmod(r, 50)
    sp, r = divmod(r, 10)
    cp = r
    return Coins(cp=cp, sp=sp, ep=ep, gp=gp, pp=pp)


def coin_purse_keys() -> Iterable[str]:
    return COIN_KEYS


__all__ = [
    "COINS_PER_POUND",
    "COIN_KEYS",
    "CoinKey",
    "Coins",
    "GP_VALUE",
    "InsufficientFunds",
    "add",
    "coin_purse_keys",
    "subtract",
    "total_gp",
    "weight",
]

