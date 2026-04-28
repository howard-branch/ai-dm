"""Pydantic models for AI-DM rules glossary entries.

A glossary entry is a typed wrapper around a row in
``assets/srd5_2/core/<category>.json``. Each entry pairs

* ``rules_text`` — verbatim or condensed SRD prose for LLM prompts, and
* ``effects``    — a list of typed :data:`~ai_dm.rules.glossary.effects.Effect`
  records that the engine can act on.

During migration, catalog files may also keep the legacy flat ``effects``
dict under ``effects_legacy``; consumers should call
:func:`ai_dm.rules.glossary.compat.legacy_effects` rather than reading
either field directly.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .effects import Effect


class GlossaryEntry(BaseModel):
    """Common envelope for every glossary row."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1)
    category: str = "rules_glossary"
    rules_text: str = ""
    tags: tuple[str, ...] = ()
    source: str = "SRD 5.2.1"
    effects: tuple[Effect, ...] = ()
    # Transitional: original flat dict from the pre-glossary catalog.
    effects_legacy: Optional[dict] = None


class Condition(GlossaryEntry):
    category: str = "condition"
    stackable: bool = False


class Action(GlossaryEntry):
    category: str = "action"
    economy: Optional[str] = None  # action / bonus_action / reaction / free


class Ability(GlossaryEntry):
    category: str = "ability"


class Skill(GlossaryEntry):
    category: str = "skill"
    ability: Optional[str] = None


__all__ = [
    "GlossaryEntry",
    "Condition",
    "Action",
    "Ability",
    "Skill",
]

