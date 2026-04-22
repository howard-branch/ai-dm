import json
from pathlib import Path

from ai_dm.rules.house_rules import HouseRule, HouseRuleSet, load_house_rules


def test_loader_handles_empty(tmp_path: Path):
    p = tmp_path / "hr.json"
    p.write_text(json.dumps({"house_rules": []}), encoding="utf-8")
    rs = load_house_rules(p)
    assert rs.house_rules == []
    assert rs.find("attack") is None


def test_loader_missing_file(tmp_path: Path):
    rs = load_house_rules(tmp_path / "absent.json")
    assert rs.house_rules == []


def test_loader_typed_rule(tmp_path: Path):
    payload = {
        "house_rules": [
            {
                "id": "no_double_dice",
                "applies_to": "damage",
                "override": {"crit_doubles_dice": False},
                "priority": 5,
            }
        ]
    }
    p = tmp_path / "hr.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    rs = load_house_rules(p)
    assert isinstance(rs.house_rules[0], HouseRule)
    assert rs.get("damage", "crit_doubles_dice", True) is False


def test_priority_picks_highest():
    rs = HouseRuleSet(
        house_rules=[
            HouseRule(id="a", applies_to="attack", override={"x": 1}, priority=1),
            HouseRule(id="b", applies_to="attack", override={"x": 2}, priority=10),
        ]
    )
    assert rs.get("attack", "x") == 2


def test_disabled_rule_skipped():
    rs = HouseRuleSet(
        house_rules=[
            HouseRule(id="off", applies_to="attack", override={"x": 1}, enabled=False),
        ]
    )
    assert rs.find("attack") is None

