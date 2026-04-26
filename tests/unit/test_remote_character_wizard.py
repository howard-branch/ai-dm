"""Drive ``RemoteCharacterWizard`` end-to-end against a fake client/bus."""
from __future__ import annotations

import threading

from ai_dm.orchestration.event_bus import EventBus
from ai_dm.orchestration.remote_character_wizard import (
    EVENT_REQUEST,
    EVENT_RESPONSE,
    RemoteCharacterWizard,
)


class _FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    def send_event(self, name, payload=None):
        self.sent.append((name, dict(payload or {})))
        return "evt-test"


def test_remote_wizard_returns_built_sheet_on_response() -> None:
    bus = EventBus()
    client = _FakeClient()
    wiz = RemoteCharacterWizard(event_bus=bus, client=client, pc_id="pc_human")

    def _respond_soon() -> None:
        # Wait until the wizard has emitted at least one request, then
        # publish a wizard_response on the bus.
        for _ in range(100):
            if client.sent:
                break
            threading.Event().wait(0.01)
        bus.publish(EVENT_RESPONSE, {
            "pc_id": "pc_human",
            "user_id": "user-foundry-1",
            "user_name": "Player1",
            "name": "Brigit",
            "archetype": "witch",
            "stat_bump": "cha",
            "background": "hedge_witch",
        })

    t = threading.Thread(target=_respond_soon, daemon=True)
    t.start()
    sheet = wiz.prompt_and_wait(timeout=2.0)
    t.join(timeout=1.0)

    assert sheet is not None, "wizard returned no sheet"
    assert sheet["name"] == "Brigit"
    assert sheet["class"] == "Witch"
    assert sheet["abilities"]["cha"] == 17  # base 16 + 1 bump
    assert sheet["background"] == "Hedge Witch"
    # The wizard captured who answered so the caller can bind the user.
    assert wiz.player_user_id == "user-foundry-1"
    assert wiz.player_user_name == "Player1"

    # And we did push at least one wizard_request envelope.
    assert client.sent and client.sent[0][0] == EVENT_REQUEST
    payload = client.sent[0][1]
    assert payload["pc_id"] == "pc_human"
    assert any(a["key"] == "witch" for a in payload["archetypes"])


def test_remote_wizard_cancellation_returns_none(monkeypatch) -> None:
    bus = EventBus()
    client = _FakeClient()
    wiz = RemoteCharacterWizard(event_bus=bus, client=client, pc_id="pc_human")

    # Cancellation only short-circuits when the player has explicitly
    # opted in to skipping; otherwise the wizard re-prompts.
    monkeypatch.setenv("AI_DM_ALLOW_SKIP_WIZARD", "1")

    threading.Timer(
        0.05,
        lambda: bus.publish(EVENT_RESPONSE, {"pc_id": "pc_human", "cancelled": True}),
    ).start()
    sheet = wiz.prompt_and_wait(timeout=2.0)
    assert sheet is None


def test_remote_wizard_reprompts_on_cancellation_by_default() -> None:
    """A cancellation without AI_DM_ALLOW_SKIP_WIZARD must trigger a
    fresh wizard_request carrying an error explaining the wizard is
    required, then accept a valid follow-up submission.
    """
    bus = EventBus()
    client = _FakeClient()
    wiz = RemoteCharacterWizard(event_bus=bus, client=client, pc_id="pc_human")

    sequence = [
        {"pc_id": "pc_human", "user_id": "u1", "cancelled": True},
        {
            "pc_id": "pc_human",
            "user_id": "u1",
            "name": "Brigit",
            "archetype": "witch",
            "stat_bump": "cha",
            "background": "hedge_witch",
        },
    ]

    def _drive() -> None:
        sent_count_at_start = 0
        for response in sequence:
            for _ in range(200):
                if len(client.sent) > sent_count_at_start:
                    break
                threading.Event().wait(0.01)
            sent_count_at_start = len(client.sent)
            bus.publish(EVENT_RESPONSE, response)

    t = threading.Thread(target=_drive, daemon=True)
    t.start()
    sheet = wiz.prompt_and_wait(timeout=3.0, retry_interval=0.05)
    t.join(timeout=1.0)

    assert sheet is not None, "wizard should re-prompt after cancellation"
    assert sheet["name"] == "Brigit"

    requests = [p for n, p in client.sent if n == EVENT_REQUEST]
    assert len(requests) >= 2
    second = requests[1]
    assert isinstance(second.get("errors"), list) and second["errors"]
    assert any("required" in e.lower() for e in second["errors"])


def test_remote_wizard_reprompts_on_invalid_then_accepts_valid() -> None:
    """An invalid first submission must trigger a fresh wizard_request
    that carries `errors` + `previous_answers`; a valid second submission
    then yields the sheet.
    """
    bus = EventBus()
    client = _FakeClient()
    wiz = RemoteCharacterWizard(event_bus=bus, client=client, pc_id="pc_human")

    sequence = [
        # First submission: unknown archetype + blank name
        {
            "pc_id": "pc_human",
            "user_id": "u1",
            "user_name": "Player1",
            "name": "",
            "archetype": "bard",
            "stat_bump": "cha",
            "background": "hedge_witch",
        },
        # Second submission: valid
        {
            "pc_id": "pc_human",
            "user_id": "u1",
            "user_name": "Player1",
            "name": "Brigit",
            "archetype": "witch",
            "stat_bump": "cha",
            "background": "hedge_witch",
        },
    ]

    def _drive() -> None:
        sent_count_at_start = 0
        for response in sequence:
            # Wait for the wizard to push a fresh wizard_request (i.e.
            # the count of sent envelopes must increase) before posting
            # the next response.
            for _ in range(200):
                if len(client.sent) > sent_count_at_start:
                    break
                threading.Event().wait(0.01)
            sent_count_at_start = len(client.sent)
            bus.publish(EVENT_RESPONSE, response)

    t = threading.Thread(target=_drive, daemon=True)
    t.start()
    sheet = wiz.prompt_and_wait(timeout=3.0, retry_interval=0.05)
    t.join(timeout=1.0)

    assert sheet is not None, "wizard returned no sheet after reprompt"
    assert sheet["name"] == "Brigit"
    assert sheet["class"] == "Witch"

    # The second wizard_request must have included `errors` and
    # `previous_answers` so the dialog can re-render with the player's
    # original input + a clear failure message.
    requests = [p for n, p in client.sent if n == EVENT_REQUEST]
    assert len(requests) >= 2
    second = requests[1]
    assert isinstance(second.get("errors"), list) and second["errors"]
    assert second.get("previous_answers", {}).get("archetype") == "bard"
    assert second.get("previous_answers", {}).get("name") == ""

