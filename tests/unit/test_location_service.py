import pytest

from ai_dm.foundry.errors import RegistryMissError
from ai_dm.game.location_model import Anchor, SceneLocation, Zone
from ai_dm.game.location_service import LocationService


def _scene() -> SceneLocation:
    return SceneLocation(
        scene_id="s1",
        anchors=[
            Anchor(id="a1", name="Altar", scene_id="s1", x=100, y=200),
            Anchor(id="a2", name="main_door", scene_id="s1", x=10, y=20),
        ],
        zones=[
            Zone(id="z1", name="hall", scene_id="s1", shape="rect", rect=(0, 0, 500, 500)),
        ],
    )


def test_resolve_anchor_case_insensitive():
    svc = LocationService()
    svc.load_scene(_scene())
    assert svc.resolve_anchor("s1", "altar") == (100, 200)
    assert svc.resolve_anchor("s1", "ALTAR") == (100, 200)
    assert svc.resolve_anchor("s1", "a1") == (100, 200)


def test_resolve_unknown_anchor_raises():
    svc = LocationService()
    svc.load_scene(_scene())
    with pytest.raises(RegistryMissError):
        svc.resolve_anchor("s1", "ghost")


def test_zones_at():
    svc = LocationService()
    svc.load_scene(_scene())
    assert any(z.id == "z1" for z in svc.zones_at("s1", 50, 50))
    assert svc.zones_at("s1", 999, 999) == []


def test_snapshot_round_trip():
    svc = LocationService()
    svc.load_scene(_scene())
    other = LocationService()
    other.restore(svc.snapshot())
    assert other.resolve_anchor("s1", "altar") == (100, 200)

