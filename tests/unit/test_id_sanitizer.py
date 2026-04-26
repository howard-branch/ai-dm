"""Tests for the id-stripping sanitizer used before TTS."""
from __future__ import annotations

from ai_dm.audio.narration_dispatcher import _strip_ids_for_speech


def test_strips_id_framing() -> None:
    text = "You are Brigit (id: aB7xQ2pM9KvLnR4t)."
    assert _strip_ids_for_speech(text) == "You are Brigit."


def test_strips_actor_id_framing_with_brackets() -> None:
    text = "Brigit [actor_id: AbC123Xyz789Qwe] turns around."
    assert _strip_ids_for_speech(text) == "Brigit turns around."


def test_replaces_bare_hash_with_someone() -> None:
    text = "You are aB7xQ2pM9KvLnR4t today."
    assert _strip_ids_for_speech(text) == "You are someone today."


def test_keeps_normal_capitalised_words_intact() -> None:
    # "Northumberland" is 14 chars but no digit → not a hash.
    assert (
        _strip_ids_for_speech("You wander into Northumberland.")
        == "You wander into Northumberland."
    )


def test_empty_input_unchanged() -> None:
    assert _strip_ids_for_speech("") == ""


def test_collapses_double_spaces_after_removal() -> None:
    text = "Hello (id: AaaBbb111Ccc) world."
    assert _strip_ids_for_speech(text) == "Hello world."

