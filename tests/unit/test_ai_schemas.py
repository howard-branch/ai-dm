import pytest

from ai_dm.ai.schemas import AIOutput, NPCDialogueLine


def test_ai_output_defaults_round_trip():
    out = AIOutput(narration="hi")
    dumped = out.model_dump()
    again = AIOutput.model_validate(dumped)
    assert again.narration == "hi"
    assert again.schema_version == "2.0"
    assert again.dialogue == []


def test_ai_output_extra_keys_forbidden():
    with pytest.raises(Exception):
        AIOutput.model_validate({"narration": "x", "wat": 1})


def test_dialogue_round_trip():
    out = AIOutput(
        narration="ok",
        dialogue=[NPCDialogueLine(npc_id="g1", text="hi", tone="gruff")],
    )
    again = AIOutput.model_validate(out.model_dump())
    assert again.dialogue[0].npc_id == "g1"
    assert again.dialogue[0].tone == "gruff"

