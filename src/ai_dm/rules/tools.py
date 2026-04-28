"""SRD 5.2.1 tools, gaming sets, musical instruments, and kits.

Single source of truth: ``assets/srd5_2/core/tools.json``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai_dm.rules.srd_core import load

_DATA = load("tools")

ToolCategory = Literal["artisan", "gaming", "musical", "kit"]


@dataclass(frozen=True)
class Tool:
    key: str
    name: str
    category: ToolCategory
    cost_amount: float
    cost_unit: str
    weight: float
    proficiency_group: str
    ability_default: str | None


_BY_KEY: dict[str, Tool] = {
    rec["key"]: Tool(
        key=rec["key"],
        name=rec["name"],
        category=rec["category"],
        cost_amount=float((rec.get("cost") or {}).get("amount", 0)),
        cost_unit=str((rec.get("cost") or {}).get("unit", "gp")),
        weight=float(rec.get("weight", 0)),
        proficiency_group=str(rec.get("proficiency_group") or rec["key"]),
        ability_default=rec.get("ability_default"),
    )
    for rec in _DATA["tools"]
}


def get_tool(key: str) -> Tool | None:
    return _BY_KEY.get(key)


def all_tools() -> list[Tool]:
    return list(_BY_KEY.values())


def is_proficient(tool_key: str, proficiencies: list[str]) -> bool:
    """True if the actor's proficiency list covers this tool's group."""
    rec = _BY_KEY.get(tool_key)
    if rec is None:
        return False
    return rec.proficiency_group in proficiencies or tool_key in proficiencies


__all__ = ["Tool", "ToolCategory", "all_tools", "get_tool", "is_proficient"]

