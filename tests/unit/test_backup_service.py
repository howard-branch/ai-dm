from pathlib import Path

from ai_dm.persistence.backups import BackupService


def test_rotate_returns_none_when_source_missing(tmp_path: Path):
    svc = BackupService(tmp_path / "bk")
    assert svc.rotate(tmp_path / "missing.json") is None


def test_rotation_prunes_to_max(tmp_path: Path):
    src = tmp_path / "save.json"
    src.write_text("{}")
    svc = BackupService(tmp_path / "bk", max_backups=3)
    paths = []
    for _ in range(6):
        # mutate so timestamps are unique enough
        src.write_text(f'{{"i":{_}}}')
        paths.append(svc.rotate(src))
    backups = sorted((tmp_path / "bk").glob("save.*.json"))
    assert len(backups) == 3

