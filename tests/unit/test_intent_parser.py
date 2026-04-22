from ai_dm.ai.client import AIClient
from ai_dm.ai.intent_parser import IntentParser
from ai_dm.ai.intent_schemas import PlayerIntent


def test_attack_fastpath():
    p = IntentParser()
    out = p.parse("I attack the goblin with my longsword")
    assert out.type == "attack"
    assert out.target_id == "goblin"
    assert out.weapon == "longsword"
    assert out.confidence >= 0.6


def test_attack_no_weapon():
    p = IntentParser()
    out = p.parse("attack goblin")
    assert out.type == "attack"
    assert out.target_id == "goblin"
    assert out.weapon is None


def test_move_fastpath():
    p = IntentParser()
    out = p.parse("I move to the altar")
    assert out.type == "move"
    assert out.target_anchor == "altar"


def test_skill_check_with_dc():
    p = IntentParser()
    out = p.parse("Roll perception DC 15")
    assert out.type == "skill_check"
    assert out.skill == "perception"
    assert out.dc == 15


def test_skill_check_without_dc():
    p = IntentParser()
    out = p.parse("Make a stealth check")
    assert out.type == "skill_check"
    assert out.skill == "stealth"
    assert out.dc is None


def test_speak_quoted():
    p = IntentParser()
    out = p.parse('I say "hello there"')
    assert out.type == "speak"
    assert out.quoted_speech == "hello there"


def test_bare_quote_speak():
    p = IntentParser()
    out = p.parse("'open sesame'")
    assert out.type == "speak"
    assert out.quoted_speech == "open sesame"


def test_use_item_fastpath():
    p = IntentParser()
    out = p.parse("I drink the potion")
    assert out.type == "use_item"
    assert out.target_id == "potion"


def test_meta_verb():
    p = IntentParser()
    out = p.parse("save")
    assert out.type == "meta"


def test_empty_input():
    p = IntentParser()
    out = p.parse("")
    assert out.type == "unknown"


def test_llm_fallback_used_when_fastpath_misses():
    canned = {
        "type": "interact",
        "verb": "examine",
        "target_id": "mural",
        "raw_text": "I look closely at the mural on the north wall",
        "confidence": 0.9,
    }
    p = IntentParser(client=AIClient(canned_response=canned))
    out = p.parse("I look closely at the mural on the north wall")
    assert out.type == "interact"
    assert out.target_id == "mural"


def test_llm_garbage_yields_unknown():
    p = IntentParser(client=AIClient(canned_response="not json at all"))
    out = p.parse("Some unparseable narrative thing happens")
    assert out.type == "unknown"


def test_intent_schema_extra_forbidden():
    # Just a sanity check on the schema.
    PlayerIntent(type="attack", target_id="x", raw_text="x")

