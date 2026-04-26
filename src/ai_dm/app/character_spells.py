"""Spell catalog + starting-spell helpers for the character wizard.

Casting styles
--------------

D&D 5e splits casters into two families:

* ``"known"`` (sorcerer / warlock / bard / ranger): the spells the
  character learns are immutable between long rests; every learned
  spell is always available to cast. We mirror this by emitting
  ``known: [...]`` with an empty ``prepared`` list.

* ``"prepared"`` (cleric / druid / wizard / paladin): the character
  has a larger ``spellbook`` (or full class list) and chooses a daily
  subset to be ``prepared``. We mirror this by emitting
  ``spellbook: [...]`` plus ``prepared: [...]`` (a subset of the
  spellbook, capped per-day by :func:`prepare_spells`).

Per-spell ``prepared`` flags are also written so callers iterating a
flat ``known + prepared`` list can tell at a glance what's castable
right now. The legacy v1 shape (``known == prepared`` for everyone) is
still produced when ``casting_style`` is omitted, for backwards
compatibility with existing sheets.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ai_dm.app.catalog_loader import load_overlay

if TYPE_CHECKING:
    from ai_dm.campaign.pack import CampaignPack

CastingStyle = Literal["known", "prepared"]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SPELLS_PATH = _REPO_ROOT / "assets" / "rules" / "dnd5e_spells.json"


def _strip_doc(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def load_spell_catalog(
    path: Path | None = None,
    *,
    pack: "CampaignPack | None" = None,
) -> dict[str, dict[str, Any]]:
    if path is not None:
        if not path.exists():
            return {}
        with path.open(encoding="utf-8") as fh:
            return _strip_doc(json.load(fh))
    return load_overlay("dnd5e_spells.json", pack=pack)


def available_spells(
    archetype_key: str,
    level: int,
    *,
    catalog: dict[str, dict[str, Any]] | None = None,
    pack: "CampaignPack | None" = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Return ``[(spell_id, spell_record)]`` learnable by ``archetype_key`` at ``level``.

    A spell with no ``archetypes`` field is open to every caster.
    """
    catalog = catalog if catalog is not None else load_spell_catalog(pack=pack)
    out: list[tuple[str, dict[str, Any]]] = []
    for sid, rec in catalog.items():
        if int(rec.get("level", -1)) != level:
            continue
        allowed = rec.get("archetypes")
        if allowed and archetype_key not in allowed:
            continue
        out.append((sid, rec))
    out.sort(key=lambda kv: kv[1].get("name", kv[0]))
    return out


def _materialise_spell(
    spell_id: str, rec: dict[str, Any], *, prepared: bool
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": spell_id,
        "name": rec.get("name", spell_id),
        "level": int(rec.get("level", 0)),
        "school": rec.get("school", ""),
        "casting_time": rec.get("casting_time", "action"),
        "range": rec.get("range", ""),
        "components": dict(rec.get("components") or {}),
        "duration": rec.get("duration", "instantaneous"),
        "prepared": bool(prepared),
    }
    if "damage" in rec:
        out["damage"] = rec["damage"]
    if "description" in rec:
        out["description"] = rec["description"]
    return out


def pick_starting_spells(
    archetype_key: str,
    cantrip_picks: list[str] | None,
    spell_picks: list[str] | None,
    *,
    spellcasting: dict[str, Any] | None,
    casting_style: CastingStyle | None = None,
    catalog: dict[str, dict[str, Any]] | None = None,
    pack: "CampaignPack | None" = None,
) -> tuple[dict[str, Any], list[str]]:
    """Build the ``spells`` block from player picks.

    Returns ``(spells_block, errors)``. Non-casters return an empty
    block. Picks beyond the archetype caps (``cantrips_known`` /
    ``spells_known``) are dropped with an error message.

    The shape of the returned block depends on ``casting_style``:

    * ``"known"`` — ``known`` holds the picked spells (immutable),
      ``prepared`` is empty, no ``spellbook`` is written.
    * ``"prepared"`` — ``spellbook`` holds the picked spells,
      ``prepared`` defaults to the same list (since at level 1 the
      preparation cap typically equals the spellbook size), ``known``
      is empty.
    * ``None`` (legacy) — both ``known`` and ``prepared`` hold the
      picked list (the v1 shape, kept for backwards compatibility).

    Each spell entry's ``prepared`` flag tracks whether it's currently
    castable — always ``True`` for ``"known"`` style; for ``"prepared"``
    it mirrors membership in the ``prepared`` list.
    """
    if not spellcasting:
        return ({"cantrips_known": [], "prepared": [], "known": [], "slots": {}}, [])

    catalog = catalog if catalog is not None else load_spell_catalog(pack=pack)
    errors: list[str] = []
    cantrips_cap = int(spellcasting.get("cantrips_known", 0))
    spells_cap = int(spellcasting.get("spells_known", 0))
    slots = dict(spellcasting.get("slots") or {})

    def _resolve(picks: list[str] | None, level: int, cap: int, prepared: bool) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in (picks or [])[:cap]:
            sid = str(raw).strip()
            if not sid or sid in seen:
                continue
            rec = catalog.get(sid)
            if rec is None or int(rec.get("level", -1)) != level:
                errors.append(f"unknown level-{level} spell {sid!r}")
                continue
            allowed = rec.get("archetypes")
            if allowed and archetype_key not in allowed:
                errors.append(f"{rec.get('name', sid)} is not available to {archetype_key}")
                continue
            seen.add(sid)
            out.append(_materialise_spell(sid, rec, prepared=prepared))
        if picks and len(picks) > cap:
            errors.append(
                f"too many level-{level} spells picked ({len(picks)}); cap is {cap}. Extras dropped."
            )
        return out

    cantrips = _resolve(cantrip_picks, 0, cantrips_cap, prepared=True)

    if casting_style == "known":
        # Sorcerer / warlock / bard family: spells are always castable.
        leveled = _resolve(spell_picks, 1, spells_cap, prepared=True)
        block: dict[str, Any] = {
            "cantrips_known": cantrips,
            "casting_style": "known",
            "known": leveled,
            "prepared": [],
            "slots": _materialise_slots(slots),
        }
    elif casting_style == "prepared":
        # Cleric / druid / wizard family: spellbook + daily prepared subset.
        # At level 1 the prep cap typically equals the picks, so we
        # default ``prepared`` to the full spellbook; ``prepare_spells``
        # narrows it later when the cap < spellbook size.
        spellbook = _resolve(spell_picks, 1, spells_cap, prepared=True)
        prepared = [dict(s) for s in spellbook]
        block = {
            "cantrips_known": cantrips,
            "casting_style": "prepared",
            "spellbook": spellbook,
            "prepared": prepared,
            "known": [],
            "slots": _materialise_slots(slots),
        }
    else:
        # Legacy v1 shape: undifferentiated. Preserved so existing
        # callers (and existing saved sheets) keep working.
        leveled = _resolve(spell_picks, 1, spells_cap, prepared=True)
        block = {
            "cantrips_known": cantrips,
            "prepared": list(leveled),
            "known": list(leveled),
            "slots": _materialise_slots(slots),
        }
    return block, errors


def _materialise_slots(slots: dict[str, Any]) -> dict[str, dict[str, int]]:
    return {str(k): {"max": int(v), "value": int(v)} for k, v in slots.items()}


def prepare_spells(
    spells_block: dict[str, Any],
    picks: list[str],
    *,
    cap: int | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Re-write ``spells_block['prepared']`` from ``picks`` (a list of
    spell ids drawn from ``spellbook``).

    Only meaningful for ``"prepared"`` archetypes; calling this on a
    ``"known"`` block returns the input unchanged plus an error. Returns
    ``(updated_block, errors)``. The input block is not mutated.

    * Unknown ids (not in the spellbook) are dropped with an error.
    * Duplicates are de-duped silently.
    * If ``cap`` is given, picks beyond it are dropped with an error.
    """
    style = spells_block.get("casting_style")
    if style == "known":
        return spells_block, ["cannot prepare spells for a 'known' caster"]
    if style != "prepared":
        # Legacy block — best-effort: treat ``known`` as the spellbook.
        spellbook = list(spells_block.get("spellbook") or spells_block.get("known") or [])
    else:
        spellbook = list(spells_block.get("spellbook") or [])

    by_id: dict[str, dict[str, Any]] = {}
    for s in spellbook:
        sid = str(s.get("id", ""))
        if sid:
            by_id[sid] = s

    errors: list[str] = []
    seen: set[str] = set()
    prepared: list[dict[str, Any]] = []
    for raw in picks or []:
        sid = str(raw).strip()
        if not sid or sid in seen:
            continue
        if sid not in by_id:
            errors.append(f"{sid!r} is not in the spellbook")
            continue
        if cap is not None and len(prepared) >= cap:
            errors.append(
                f"prepared cap is {cap}; dropping extra picks beyond it"
            )
            break
        seen.add(sid)
        entry = dict(by_id[sid])
        entry["prepared"] = True
        prepared.append(entry)

    # Re-mark the spellbook entries' ``prepared`` flag for consistency.
    new_spellbook = []
    for s in spellbook:
        copy = dict(s)
        copy["prepared"] = str(copy.get("id", "")) in seen
        new_spellbook.append(copy)

    out = dict(spells_block)
    out["spellbook"] = new_spellbook
    out["prepared"] = prepared
    return out, errors


