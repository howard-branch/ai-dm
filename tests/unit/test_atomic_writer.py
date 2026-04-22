import json
from pathlib import Path

import pytest

from ai_dm.persistence.atomic_writer import atomic_write_json


def test_atomic_write_creates_file(tmp_path: Path):
    target = tmp_path / "out.json"
    atomic_write_json(target, {"a": 1})
    assert json.loads(target.read_text()) == {"a": 1}


def test_atomic_write_preserves_old_on_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "out.json"
    atomic_write_json(target, {"v": 1})

    import ai_dm.persistence.atomic_writer as aw

    def boom(*a, **kw):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(aw.os, "replace", boom)
    with pytest.raises(RuntimeError):
        atomic_write_json(target, {"v": 2})

    # old contents intact
    assert json.loads(target.read_text()) == {"v": 1}
    # no leftover .tmp files
    assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())

