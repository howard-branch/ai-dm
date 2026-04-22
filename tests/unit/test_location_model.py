import pytest

from ai_dm.game.location_model import Anchor, SceneLocation, Zone


def test_rect_zone_contains():
    z = Zone(id="z", name="z", scene_id="s", shape="rect", rect=(0, 0, 10, 10))
    assert z.contains(5, 5)
    assert not z.contains(11, 5)
    assert z.centroid() == (5, 5)


def test_polygon_zone_contains_concave():
    poly = [(0, 0), (10, 0), (10, 10), (5, 5), (0, 10)]
    z = Zone(id="z", name="z", scene_id="s", shape="polygon", polygon=poly)
    assert z.contains(2, 2)
    assert z.contains(8, 2)
    assert not z.contains(5, 8)  # concave dip


def test_invalid_rect_rejected():
    with pytest.raises(Exception):
        Zone(id="z", name="z", scene_id="s", shape="rect", rect=(10, 10, 0, 0))


def test_scene_location_round_trip():
    sl = SceneLocation(
        scene_id="s1",
        anchors=[Anchor(id="a1", name="altar", scene_id="s1", x=1, y=2)],
        zones=[Zone(id="z1", name="hall", scene_id="s1", shape="rect", rect=(0, 0, 10, 10))],
    )
    again = SceneLocation.model_validate(sl.model_dump())
    assert again.anchors[0].name == "altar"

