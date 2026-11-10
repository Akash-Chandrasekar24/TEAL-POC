"""A large outline on a datum plane is sketched with ONE add_sketch_entities call (the checklist graph sends each segment as its own call:
~1.7 s each live, a 192-segment rotor flank timed out). Positions go through the sketch's real frame, as for a face sketch."""
import asyncio
import json
from types import SimpleNamespace

import fakes  # noqa: F401
from app.menu import parse_reply
from app.runner import prepare


def boss(shape):
    bl = parse_reply({"list_version": "1", "unit": "mm", "projection": "third", "items": [{"id": "F1", "feature": "extrude_profile_boss", "params": {"profile": shape, "depth": 42}}]})
    return prepare(bl)[0].feature


def test_a_rotor_flank_is_one_bulk_sketch_and_a_rectangle_stays_on_the_checklist():
    rotor = boss({"shape": "rotor_flank", "generating_radius": 60, "eccentricity": 10})
    assert rotor.bulk() and not boss({"shape": "rectangle", "width": 40, "height": 20}).bulk()
    sent = []

    class SW:
        async def create_sketch(self, plane=None, **kw):
            sent.append(("create_sketch", plane))
            frame = {"frame": {"origin": [0, 0, 0], "xdir": [1, 0, 0], "ydir": [0, 1, 0]}}
            return SimpleNamespace(ok=True, text="COMPLETED | result_geometry=" + json.dumps(frame))

        async def add_sketch_entities(self, segments):
            sent.append(("add_sketch_entities", len(json.loads(segments))))
            return SimpleNamespace(ok=True, text="COMPLETED")

        async def extrude_feature(self, feature_type=None, depth=None, **kw):
            sent.append(("extrude_feature", feature_type, round(depth, 6)))
            return SimpleNamespace(ok=True, text="COMPLETED")

        async def submit_feature_graph(self, *a, **kw):
            raise AssertionError("a large outline must not go through the per-segment checklist")

    asyncio.run(rotor._bulk_action(SimpleNamespace(sw=SW()), rotor.validate()))
    n = len(__import__("app.features.base", fromlist=["profile_to_graph"]).profile_to_graph(rotor.profile, "mm", rotor.origin))
    assert sent == [("create_sketch", "Front Plane"), ("add_sketch_entities", n), ("extrude_feature", "boss", 0.042)] and n > 24
