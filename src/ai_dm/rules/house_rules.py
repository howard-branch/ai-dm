"""Typed house-rules schema + loader.

House rules are authored as JSON under ``assets/rules/house_rules.json``::

    {
      "house_rules": [
        {
          "id": "crit_double_dice",
          "applies_to": "attack",
          "override": {"crit_doubles_dice": true},
          "priority": 10,
          "description": "Critical hits double the damage dice (RAW)."
        }
      ]
    }

The ``RulesEngine`` consults :meth:`HouseRuleSet.find` before applying its
default 5e behaviour.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("ai_dm.rules.house_rules")

AppliesTo = Literal[
    "ability_check",
    "saving_throw",
    "attack",
    "damage",
    "condition",
    "initiative",
    "any",
]


class HouseRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    applies_to: AppliesTo
    override: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    description: str = ""
    enabled: bool = True


class HouseRuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    house_rules: list[HouseRule] = Field(default_factory=list)

    def find(self, applies_to: AppliesTo, key: str | None = None) -> HouseRule | None:
        """Return the highest-priority enabled rule matching ``applies_to``.

        If ``key`` is provided, also requires that key to be present in
        the rule's ``override`` dict.
        """
        candidates = [
            r
            for r in self.house_rules
            if r.enabled and r.applies_to in (applies_to, "any")
            and (key is None or key in r.override)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda r: r.priority, reverse=True)
        return candidates[0]

    def get(self, applies_to: AppliesTo, key: str, default: Any = None) -> Any:
        rule = self.find(applies_to, key)
        if rule is None:
            return default
        return rule.override.get(key, default)


def load_house_rules(path: Path | None) -> HouseRuleSet:
    """Load house rules from ``path``. Missing/invalid files yield an empty set."""
    if path is None or not Path(path).exists():
        return HouseRuleSet()
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to read house_rules from %s: %s", path, exc)
        return HouseRuleSet()
    try:
        return HouseRuleSet.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("invalid house_rules payload in %s: %s", path, exc)
        return HouseRuleSet()

