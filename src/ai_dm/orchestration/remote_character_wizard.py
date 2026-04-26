"""Run the character-creation wizard through the Foundry browser.

Sends a ``wizard_request`` event over the relay (which the Foundry
module renders as a Dialog), waits for the player's ``wizard_response``
event to come back through the in-process EventBus, and returns a
sheet built by the existing pure :func:`build_sheet` helper.

Re-emits the request periodically until a response arrives so the
prompt survives the Foundry browser tab being opened *after* Python
starts.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING, Any

from ai_dm.app.character_equipment import load_items_catalog, load_starting_kits
from ai_dm.app.character_spells import load_spell_catalog
from ai_dm.app.character_wizard import (
    ARCHETYPES,
    BACKGROUNDS,
    build_sheet,
    validate_response,
)

if TYPE_CHECKING:
    from ai_dm.campaign.pack import CampaignPack

logger = logging.getLogger("ai_dm.orchestration.remote_character_wizard")

EVENT_REQUEST = "wizard_request"
EVENT_RESPONSE = "foundry.wizard_response"


def _menu_payload(pack: "CampaignPack | None" = None) -> dict[str, Any]:
    from ai_dm.app.character_wizard import _spellcasting_for  # local to avoid cycles

    items_catalog = load_items_catalog(pack=pack)
    spell_catalog = load_spell_catalog(pack=pack)
    kits = load_starting_kits(pack=pack)
    return {
        "archetypes": [
            {
                "key": a.key,
                "label": a.label,
                "emoji": a.emoji,
                "blurb": a.blurb,
                "abilities": dict(a.abilities),
                # Per-level spellcasting block derived from the SRD progression
                # tables (level 1 by default — the wizard is for new PCs).
                "spellcasting": _spellcasting_for(a, level=1),
            }
            for a in ARCHETYPES.values()
        ],
        "backgrounds": [
            {"key": b.key, "label": b.label, "blurb": b.blurb}
            for b in BACKGROUNDS.values()
        ],
        "abilities": ["str", "dex", "con", "int", "wis", "cha"],
        "items": items_catalog,
        "kits": kits,
        "spells": spell_catalog,
    }


class RemoteCharacterWizard:
    """Drive the character wizard via the Foundry browser."""

    def __init__(
        self,
        *,
        event_bus,
        client,
        pc_id: str,
        pack: "CampaignPack | None" = None,
    ) -> None:
        self.event_bus = event_bus
        self.client = client
        self.pc_id = pc_id
        self.pack = pack
        self._response: dict[str, Any] | None = None
        self._done = threading.Event()
        self._unsub = None
        # Populated from the wizard_response so the caller can later
        # send an ``assign_player_character`` event back to Foundry.
        self.player_user_id: str | None = None
        self.player_user_name: str | None = None

    # ------------------------------------------------------------------ #

    def _on_response(self, payload: dict[str, Any]) -> None:
        # Ignore responses that aren't for our PC (defensive — the
        # Foundry side echoes the pc_id back).
        target = payload.get("pc_id")
        if target and target != self.pc_id:
            logger.info(
                "ignoring wizard_response for pc_id=%r (waiting for %r)",
                target, self.pc_id,
            )
            return
        logger.info(
            "received wizard_response: cancelled=%s user=%s name=%r archetype=%s background=%s",
            bool(payload.get("cancelled")),
            payload.get("user_id"),
            payload.get("name"),
            payload.get("archetype"),
            payload.get("background"),
        )
        self._response = payload
        self._done.set()

    def prompt_and_wait(
        self,
        *,
        timeout: float | None = None,
        retry_interval: float = 5.0,
    ) -> dict[str, Any] | None:
        """Block until the player submits a *valid* Foundry wizard dialog.

        On invalid submissions we re-emit ``wizard_request`` with an
        ``errors`` list and the player's previous answers so the dialog
        can be re-shown with the offending fields highlighted. Loops
        until either:

          * a valid sheet can be built — returns it,
          * the player cancels — returns ``None``,
          * the overall ``timeout`` (if set) elapses — returns ``None``.
        """
        self._unsub = self.event_bus.subscribe(EVENT_RESPONSE, self._on_response)
        try:
            errors: list[str] = []
            previous: dict[str, Any] = {}
            elapsed = 0.0

            while True:
                # Reset for the next round.
                self._response = None
                self._done.clear()

                request_payload = {
                    "pc_id": self.pc_id,
                    **_menu_payload(self.pack),
                }
                if errors:
                    request_payload["errors"] = list(errors)
                    request_payload["previous_answers"] = dict(previous)

                # Inner re-emit + wait loop until *some* response arrives.
                while not self._done.is_set():
                    try:
                        self.client.send_event(EVENT_REQUEST, request_payload)
                        logger.info(
                            "sent wizard_request to Foundry (pc_id=%s, errors=%d); waiting…",
                            self.pc_id, len(errors),
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "wizard_request send failed (will retry): %s", exc,
                        )
                    step = retry_interval
                    if timeout is not None:
                        remaining = timeout - elapsed
                        if remaining <= 0:
                            logger.warning("remote character wizard timed out")
                            return None
                        step = min(step, remaining)
                    if self._done.wait(timeout=step):
                        break
                    elapsed += step

                payload = self._response or {}
                # Always remember who answered so the caller can bind the user.
                if payload.get("user_id"):
                    self.player_user_id = str(payload["user_id"])
                if payload.get("user_name"):
                    self.player_user_name = str(payload["user_name"])

                if payload.get("cancelled"):
                    if os.environ.get("AI_DM_ALLOW_SKIP_WIZARD", "").strip() in {"1", "true", "yes"}:
                        logger.info("remote character wizard cancelled by player (skip allowed)")
                        return None
                    logger.warning(
                        "remote character wizard cancelled by player — re-prompting "
                        "(set AI_DM_ALLOW_SKIP_WIZARD=1 to allow skipping)"
                    )
                    errors = [
                        "Character creation is required to start the adventure. "
                        "Please complete the form below."
                    ]
                    previous = {
                        k: payload.get(k)
                        for k in ("name", "archetype", "stat_bump", "background", "purchases", "cantrip_picks", "spell_picks")
                    }
                    continue

                # Validate before trying to build — gives the player a
                # readable error instead of a silent KeyError log.
                errors = validate_response(payload)
                if errors:
                    logger.info(
                        "wizard response invalid (%d error(s)) — re-prompting: %s",
                        len(errors), errors,
                    )
                    previous = {
                        k: payload.get(k)
                        for k in ("name", "archetype", "stat_bump", "background", "purchases", "cantrip_picks", "spell_picks")
                    }
                    continue  # outer loop: re-emit with errors

                try:
                    sheet = build_sheet(
                        self.pc_id,
                        str(payload.get("name") or "").strip() or self.pc_id,
                        str(payload.get("archetype") or "").strip().lower(),
                        (str(payload.get("stat_bump")).strip().lower()
                         if payload.get("stat_bump") else None),
                        str(payload.get("background") or "").strip().lower(),
                        purchases=payload.get("purchases") or None,
                        cantrip_picks=payload.get("cantrip_picks") or None,
                        spell_picks=payload.get("spell_picks") or None,
                        pack=self.pack,
                    )
                except KeyError as exc:
                    # Defensive: validate_response should have caught this.
                    logger.warning(
                        "wizard response had unknown key %s after validation: %s",
                        exc, payload,
                    )
                    errors = [f"Unknown value: {exc}. Please pick from the menu."]
                    previous = {
                        k: payload.get(k)
                        for k in ("name", "archetype", "stat_bump", "background", "purchases", "cantrip_picks", "spell_picks")
                    }
                    continue

                return sheet
        finally:
            if self._unsub is not None:
                try:
                    self._unsub()
                except Exception:  # noqa: BLE001
                    pass
                self._unsub = None

