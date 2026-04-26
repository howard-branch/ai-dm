"""Lobby gate: wait for the GM to confirm "everyone is here" before
running the world setup batch.

Without this the bootstrap sprints straight from "Python started" to
"create scene, spawn tokens, push opening narration" — which means the
opener is gone before late-arriving players load Foundry, and the GM
has no opportunity to say "hold on, I'm waiting on Bob".

Flow
----
1. Python polls the relay census every ``poll_interval`` seconds.
2. Each poll publishes a ``lobby_status`` Foundry event whispered to
   the GM, listing connected players and (if the manifest declares
   ``start.expected_players``) who is still missing.
3. Python blocks on a :class:`threading.Event` that is set when:

   * a ``foundry.start_game`` event arrives (GM typed ``/startgame``), OR
   * ``AI_DM_AUTOSTART=1`` was set in the environment, OR
   * all expected players + the GM have been connected continuously
     for ``auto_grace`` seconds.

Returns ``True`` if the lobby is ready, ``False`` if the wait timed out
or no relay client is available (in which case the caller should still
proceed best-effort, matching the old behaviour).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from ai_dm.app.container import Container
from ai_dm.campaign.pack import CampaignPack

logger = logging.getLogger("ai_dm.app.lobby")


@dataclass
class LobbyConfig:
    poll_interval: float = 2.0
    timeout: float = 600.0           # 10 min hard cap
    auto_grace: float = 5.0          # everyone present for this long → auto-start


def _expected_players(pack: CampaignPack) -> list[str]:
    """Names of Foundry users we wait for. Pulled from
    ``manifest.start.expected_players`` (a list of strings).
    """
    raw = (pack.manifest.start or {}).get("expected_players") or []
    return [str(n).strip() for n in raw if str(n).strip()]


def _summarise(census: dict[str, Any] | None,
               expected: list[str]) -> dict[str, Any]:
    if not census:
        return {
            "gm_present": False,
            "connected": [],
            "missing": list(expected),
            "expected": list(expected),
            "ready": False,
        }
    clients = census.get("foundry_clients") or []
    connected_names = sorted({c.get("user_name") for c in clients if c.get("user_name")})
    gm_present = census.get("foundry_gm_count", 0) > 0
    missing = [n for n in expected if n not in connected_names] if expected else []
    ready = gm_present and not missing
    return {
        "gm_present": gm_present,
        "connected": connected_names,
        "missing": missing,
        "expected": list(expected),
        "ready": ready,
    }


def wait_for_lobby_ready(
    pack: CampaignPack,
    container: Container,
    *,
    config: LobbyConfig | None = None,
) -> bool:
    """Block until the GM signals start (or the auto-start conditions hold).

    Returns ``True`` if we should proceed with world setup, ``False`` if
    we couldn't even reach the relay (caller should still fall through
    so the existing GM-census fail-fast in ``_apply_hardcoded_start``
    can produce its own diagnostic).
    """
    cfg = config or LobbyConfig()
    if container.client is None or container.event_bus is None:
        return False

    # Hard override: skip the gate entirely.
    if os.environ.get("AI_DM_AUTOSTART", "").strip().lower() in {"1", "true", "yes"}:
        logger.info("lobby: AI_DM_AUTOSTART=1 — skipping lobby gate")
        return True

    expected = _expected_players(pack)

    started = threading.Event()
    started_by: dict[str, str] = {}

    def _on_start(payload: dict) -> None:
        started_by["user"] = str(payload.get("user_name") or payload.get("user_id") or "?")
        started.set()

    unsub = container.event_bus.subscribe("foundry.start_game", _on_start)
    logger.info(
        "lobby: waiting for GM to type /startgame "
        "(expected_players=%s, AI_DM_AUTOSTART=1 to skip)",
        expected or "<any>",
    )

    deadline = time.monotonic() + cfg.timeout
    last_summary: dict[str, Any] | None = None
    last_push_at: float = 0.0
    all_present_since: float | None = None

    # Re-publish at least this often even when nothing changes, so a GM
    # whose chat overlay has timed out / who only just joined still sees
    # the lobby. The narration-log window persists each push, so the
    # status is never lost — but the chat reminder still helps.
    REPUSH_EVERY = 30.0

    try:
        while not started.is_set():
            if time.monotonic() > deadline:
                logger.warning("lobby: wait timed out after %.0fs", cfg.timeout)
                return False

            census = container.client.who(timeout=1.0)
            summary = _summarise(census, expected)

            now = time.monotonic()
            if summary != last_summary or (now - last_push_at) >= REPUSH_EVERY:
                _push_status_event(container, pack, summary, cfg)
                last_summary = summary
                last_push_at = now

            # Auto-start path: everyone we expected is here AND the GM is
            # present, continuously for ``auto_grace`` seconds.
            if summary["ready"] and expected:
                if all_present_since is None:
                    all_present_since = time.monotonic()
                elif time.monotonic() - all_present_since >= cfg.auto_grace:
                    logger.info(
                        "lobby: all expected players present (%s) — auto-starting",
                        summary["connected"],
                    )
                    return True
            else:
                all_present_since = None

            # Wake up either when /startgame arrives or after poll_interval.
            started.wait(timeout=cfg.poll_interval)

        logger.info("lobby: /startgame received from %s — proceeding",
                    started_by.get("user", "?"))
        return True
    finally:
        try:
            unsub()
        except Exception:  # noqa: BLE001
            pass


def _push_status_event(
    container: Container,
    pack: CampaignPack,
    summary: dict[str, Any],
    cfg: LobbyConfig,
) -> None:
    """Whisper a human-readable lobby status to all GM browsers."""
    if container.client is None:
        return
    payload = {
        "campaign": pack.manifest.name or pack.slug,
        "scene_id": (pack.manifest.start or {}).get("scene"),
        "gm_present": summary["gm_present"],
        "connected": summary["connected"],
        "missing": summary["missing"],
        "expected": summary["expected"],
        "ready": summary["ready"],
        "auto_grace": cfg.auto_grace,
        "instructions": "GM: type /startgame in chat to begin.",
    }
    try:
        # event_id keyed on the summary AND a 30 s time bucket so a
        # periodic re-push of an unchanged summary gets a fresh id and
        # isn't suppressed by Foundry's seenEventIds LRU. (Within the
        # same bucket, identical summaries still dedupe — this matters
        # for the every-2 s poll loop.)
        sig = f"{int(summary['gm_present'])}|{','.join(summary['connected'])}|{','.join(summary['missing'])}"
        bucket = int(time.monotonic() // 30)
        container.client.send_event(
            "lobby_status", payload, event_id=f"lobby:{pack.slug}:{sig}:{bucket}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("lobby_status push failed: %s", exc)

