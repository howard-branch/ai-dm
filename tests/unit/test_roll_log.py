from pathlib import Path

from ai_dm.persistence.roll_log import RollLog, RollRecord


def test_roll_log_appends_jsonl_records(tmp_path: Path):
    log = RollLog(state_root=tmp_path)
    log.append(RollRecord(
        request_id="prq-1",
        source="player",
        actor_id="Sansa",
        roll_type="skill",
        key="perception",
        formula="1d20+3",
        total=17,
        modifier=3,
        rolls=[14],
        kept=[14],
        dc=15,
        success=True,
    ))
    log.append(RollRecord(
        request_id="dm-1",
        source="dm",
        actor_id="Goblin",
        roll_type="skill",
        key="stealth",
        formula="1d20+6",
        total=22,
        rolls=[16],
        kept=[16],
        dc=14,
        success=True,
        visibility="gm",
    ))
    records = list(log.iter_records())
    assert len(records) == 2
    assert records[0]["request_id"] == "prq-1"
    assert records[0]["source"] == "player"
    assert records[0]["success"] is True
    assert records[1]["visibility"] == "gm"
    # File ends in newlines; each line is a complete JSON object.
    raw = log.path.read_text(encoding="utf-8").splitlines()
    assert all(line and line.endswith("}") for line in raw)

