"""In-process registry that loads + validates SRD glossary catalog files.

Built on :func:`ai_dm.rules.srd_core.load`, the registry exposes typed
lookups for the catalogs that benefit from the
``rules_text`` + typed-``effects`` split:

* :meth:`get_condition`, :meth:`get_action`, :meth:`get_ability`,
  :meth:`get_skill` — strongly typed.
* :meth:`get` — generic lookup as a :class:`GlossaryEntry`.
* :meth:`all` — iterate a category.
* :meth:`search` — substring scan over label/rules_text/tags.
* :meth:`raw_record` — escape hatch for the migration shim.

A module-level singleton :data:`registry` is built lazily on first use.
Tests may call :meth:`reload` to pick up monkey-patched JSON.
"""
from __future__ import annotations

from threading import RLock
from typing import Any, Iterable, Optional

from ai_dm.rules.srd_core import load

from .models import Ability, Action, Condition, GlossaryEntry, Skill

# Catalogs the registry loads up-front. Reserved files (classes, feats,
# species, subclasses, backgrounds, proficiencies) are loaded on-demand
# via :meth:`raw_record` until the sheet/wizard layer is implemented.
_EAGER_CATEGORIES: dict[str, tuple[str, str]] = {
    # category : (json_filename, list_key)
    "conditions": ("conditions", "conditions"),
    "actions": ("actions", "standard_actions"),
}


class GlossaryRegistry:
    """Thread-safe lazy-loaded registry of typed glossary entries."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._raw: dict[str, dict[str, dict]] = {}
        self._typed: dict[str, dict[str, GlossaryEntry]] = {}
        self._loaded = False

    # --- internals -------------------------------------------------- #

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            for category, (filename, list_key) in _EAGER_CATEGORIES.items():
                self._load_category(category, filename, list_key)
            self._loaded = True

    def _load_category(self, category: str, filename: str, list_key: str) -> None:
        data = load(filename)
        records = list(data.get(list_key, ()) or ())
        raw_by_key: dict[str, dict] = {}
        typed_by_key: dict[str, GlossaryEntry] = {}
        model = _CATEGORY_MODEL.get(category, GlossaryEntry)
        for rec in records:
            key = str(rec.get("key", "")).strip().lower()
            if not key:
                continue
            raw_by_key[key] = rec
            try:
                typed_by_key[key] = model.model_validate(
                    {**rec, "category": category}
                )
            except Exception:
                # Pre-migration rows without typed `effects` still work
                # via the compat shim — keep them in raw, skip typed.
                pass
        self._raw[category] = raw_by_key
        self._typed[category] = typed_by_key

    # --- public API ------------------------------------------------- #

    def reload(self) -> None:
        """Drop caches; next access re-reads JSON via ``srd_core.load``."""
        with self._lock:
            self._raw.clear()
            self._typed.clear()
            self._loaded = False
            # Also bust the upstream loader cache so JSON edits are visible.
            load.cache_clear()

    def raw_record(self, category: str, key: str) -> Optional[dict]:
        """Return the raw JSON dict (a *copy*) or ``None``."""
        self._ensure_loaded()
        rec = self._raw.get(category, {}).get(key.strip().lower())
        return dict(rec) if rec is not None else None

    def get(self, category: str, key: str) -> Optional[GlossaryEntry]:
        self._ensure_loaded()
        return self._typed.get(category, {}).get(key.strip().lower())

    def all(self, category: str) -> tuple[GlossaryEntry, ...]:
        self._ensure_loaded()
        return tuple(self._typed.get(category, {}).values())

    def get_condition(self, key: str) -> Optional[Condition]:
        e = self.get("conditions", key)
        return e if isinstance(e, Condition) else None

    def get_action(self, key: str) -> Optional[Action]:
        e = self.get("actions", key)
        return e if isinstance(e, Action) else None

    def get_ability(self, key: str) -> Optional[Ability]:
        e = self.get("abilities", key)
        return e if isinstance(e, Ability) else None

    def get_skill(self, key: str) -> Optional[Skill]:
        e = self.get("skills", key)
        return e if isinstance(e, Skill) else None

    def search(self, text: str, *, categories: Iterable[str] | None = None) -> tuple[GlossaryEntry, ...]:
        """Case-insensitive substring search across label / text / tags."""
        self._ensure_loaded()
        needle = text.strip().lower()
        if not needle:
            return ()
        cats = tuple(categories) if categories else tuple(self._typed.keys())
        hits: list[tuple[int, GlossaryEntry]] = []
        for cat in cats:
            for entry in self._typed.get(cat, {}).values():
                hay_label = entry.label.lower()
                hay_text = entry.rules_text.lower()
                hay_tags = " ".join(entry.tags).lower()
                score = 0
                if needle in entry.key:
                    score += 4
                if needle in hay_label:
                    score += 3
                if needle in hay_tags:
                    score += 2
                if needle in hay_text:
                    score += 1
                if score:
                    hits.append((-score, entry))
        hits.sort(key=lambda t: (t[0], t[1].key))
        return tuple(e for _, e in hits)


_CATEGORY_MODEL: dict[str, type[GlossaryEntry]] = {
    "conditions": Condition,
    "actions": Action,
    "abilities": Ability,
    "skills": Skill,
}


# Module-level singleton.
registry = GlossaryRegistry()


__all__ = ["GlossaryRegistry", "registry"]

