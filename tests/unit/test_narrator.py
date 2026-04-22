from ai_dm.ai.client import AIClient
from ai_dm.ai.narrator import Narrator


def test_narrator_uses_canned_dict():
    nar = Narrator(client=AIClient(canned_response={"narration": "yo"}))
    out = nar.narrate("look", {})
    assert out.narration == "yo"


def test_narrator_recovers_from_garbage():
    nar = Narrator(client=AIClient(canned_response="this is not json"))
    out = nar.narrate("look", {})
    assert out.narration  # fallback narration present
    assert "parse_errors" in out.metadata

