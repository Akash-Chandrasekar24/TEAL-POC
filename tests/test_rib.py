"""A rib whose slope meets the boss wall at the boss top (the user's F5: 107 / 107) is built with a flat top inside the boss, not refused."""
from app.features.base import Position
from app.features.rib import Rib


def rib(height_at_boss):
    return Rib("mm", Position(0, 0), boss_diameter=70, boss_height=107, thickness=12, foot_distance=72,
               height_at_boss=height_at_boss, plate_thickness=12, sides="left")


def test_a_steep_rib_stops_flat_at_the_boss_top():
    r = rib(107)
    r.validate()                                                    # was: "its slope would reach 118.470 ... needs_human"
    zs = [-p for p in _ys(r.graph(-1))]
    assert max(zs) * 1000 <= 12 + 107 + 1e-6                        # nothing above the boss top
    assert r.side_volume_mm3() > 0


def test_a_gentle_rib_is_unchanged():
    r = rib(60)
    assert r.inner_height() <= r.boss_height and len(_ys(r.graph(-1))) == 6   # still the triangle (3 lines x 2 ends)


def _ys(graph):
    found = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("kind") == "line":
                found.extend([o["y1"], o["y2"]])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(graph)
    return found
