from ai_dm.ai.response_parser import parse_ai_output, safe_parse_ai_output


def test_parse_ai_output_dict():
    payload = {
        "narration": "Test",
        "spoken_dialogue": "",
        "dice_requests": [],
        "commands": [],
        "state_updates": [],
        "metadata": {},
    }
    result = parse_ai_output(payload)
    assert result.narration == "Test"


def test_safe_parse_handles_codefenced_json():
    text = '```json\n{"narration": "hi"}\n```'
    out, issues = safe_parse_ai_output(text)
    assert out.narration == "hi"
    assert issues == []


def test_safe_parse_extracts_json_with_leading_prose():
    text = 'sure! here you go: {"narration": "hello"} -- end'
    out, _ = safe_parse_ai_output(text)
    assert out.narration == "hello"


def test_safe_parse_repairs_trailing_commas():
    text = '{"narration": "hi", "commands": [],}'
    out, _ = safe_parse_ai_output(text)
    assert out.narration == "hi"


def test_safe_parse_falls_back_on_garbage():
    out, issues = safe_parse_ai_output("not json at all")
    assert out.narration  # always has something
    assert any(i.kind == "json" for i in issues)
    assert "parse_errors" in out.metadata


def test_safe_parse_falls_back_on_schema_violation():
    out, issues = safe_parse_ai_output({"narration": 123})  # wrong type
    assert any(i.kind == "schema" for i in issues)
    assert out.narration
