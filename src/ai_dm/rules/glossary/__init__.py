"""AI-DM rules glossary — typed views over ``assets/srd5_2/core/`` JSON.

Public surface:

* :data:`registry` — singleton :class:`GlossaryRegistry`.
* :class:`GlossaryEntry`, :class:`Condition`, :class:`Action`,
  :class:`Ability`, :class:`Skill` — Pydantic models.
* :data:`Effect` — discriminated union of typed effects.
* :func:`legacy_effects` — shim returning the pre-glossary flat dict.
"""
from __future__ import annotations

from .compat import legacy_effects, synthesise_legacy
from .effects import Effect
from .models import Ability, Action, Condition, GlossaryEntry, Skill
from .registry import GlossaryRegistry, registry

__all__ = [
    "Ability",
    "Action",
    "Condition",
    "Effect",
    "GlossaryEntry",
    "GlossaryRegistry",
    "Skill",
    "legacy_effects",
    "registry",
    "synthesise_legacy",
]

