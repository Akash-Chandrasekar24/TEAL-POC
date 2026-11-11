"""The elevation pass adds the side profile cut (and the full height) when the side view is not a constant height."""
import json

import fakes  # noqa: F401
from app.drawing.elevation import apply_elevation, ask_elevation, elevation_from
from app.llm.base import LLMResponse
from app.menu import parse_reply
from app.runner import prepare, validate_list

DRAFT = {"list_version": "1", "unit": "mm", "projection": "third", "seen_dimensions": ["200", "80", "32"],
         "items": [{"id": "F1", "feature": "extrude_profile_boss", "params": {"profile": {"shape": "rectangle", "width": 200, "height": 80}, "depth": 32}}]}


class Says:
    def __init__(self, data):
        self.data = data

    def generate_json(self, system, parts):
        return LLMResponse(json.dumps(self.data), self.data, "fake", 0, {})


def test_a_raised_middle_becomes_full_height_plus_a_side_profile_cut_and_a_flat_part_is_left_alone():
    clamp = {"constant": False, "full_height": 60, "evidence": "view C: 32 ends, 60 middle",
             "outline": [[0, 0], [200, 0], [200, 32], [130, 32], [110, 60], [90, 60], [70, 32], [0, 32]]}
    found = elevation_from(ask_elevation(Says(clamp), b"png"), DRAFT, 200)
    out = apply_elevation(DRAFT, found)
    assert [i["feature"] for i in out["items"]] == ["extrude_profile_boss", "side_profile_cut"] and out["items"][0]["params"]["depth"] == 60
    assert validate_list(parse_reply(out)) == [] and len(prepare(parse_reply(out))) == 1                  # base + E1 are one step (shaped_base)
    assert elevation_from({"constant": True}, DRAFT, 200) is None
    assert elevation_from({"constant": False}, DRAFT, 200) is None                         # an unusable reply changes nothing
    elbow = {**clamp, "full_height": 540, "outline": [[0, 0], [360, 0], [360, 540], [0, 540]]}
    assert elevation_from(elbow, DRAFT, 200) is None                                        # a side view 80 % longer than the base: another reading, ignored
    near = elevation_from({**clamp, "outline": [[x * 1.05, y] for x, y in clamp["outline"]]}, DRAFT, 200)
    assert max(p[0] for p in near["item"]["params"]["outline"]["points"]) == 200            # 5 % off: scaled to meet both ends of the base
