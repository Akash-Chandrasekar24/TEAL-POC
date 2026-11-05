"""Offline checks of the benchmark scorers: python tests/test_eval.py"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.score import dim_tokens, score_dimensions, score_views  # noqa: E402


def test_dimension_tokens():
    cases = {"6xØ24": ["Ø24"], "2 x Ø5.50 THRU": ["Ø5.5"], "4-R20": ["R20"], "8-Ø60": ["Ø60"], "2 holes Ø4": ["Ø4"], "R12.50": ["R12.5"],
             "80.00 ±0.10": ["80"], "(360)": ["360"], "3x45°": ["3", "45°"], "Prof. 28": ["28"], "12.00 x 37.00": ["12", "37"],
             "⌀50": ["Ø50"], "57°": ["57°"], "0": []}
    for text, expected in cases.items():
        assert dim_tokens(text) == expected, (text, dim_tokens(text))


def test_dimension_score():
    s = score_dimensions(["Ø50", "R35", "R35", "200"], ["⌀50", "R35", "200", "17"])
    assert (s["matched"], s["recall"], s["precision"]) == (3, 0.75, 0.75) and s["missed"] == ["R35"]
    assert score_dimensions(["Ø50"], ["Ø50", "9"], complete=False)["precision"] is None


def test_view_score():
    truth = [{"page": 1, "point": [10, 10]}, {"page": 1, "point": [50, 50]}, {"page": 2, "point": [5, 5]}]
    s = score_views(truth, {1: [[0, 0, 20, 20], [40, 40, 60, 60], [80, 80, 90, 90]], 2: [[0, 0, 10, 10]]})
    assert (s["found"], s["boxes"], s["empty"], s["merged"]) == (3, 4, 1, 0) and s["precision"] == 0.75
    s = score_views(truth, {1: [[0, 0, 60, 60]]})                     # two views merged into one box: neither is found
    assert s["found"] == 0 and s["merged"] == 1 and s["coverable"] == 0
    wide = [{"page": 1, "point": [50, 50], "extent": [[10, 50], [190, 50]]}]
    s = score_views(wide, {1: [[0, 0, 100, 100], [100, 0, 200, 100]]})  # a view split in two fragments: not whole, but coverable
    assert (s["found"], s["coverable"]) == (0, 1)
    assert score_views(wide, {1: [[0, 0, 200, 100]]})["found"] == 1


def test_benchmark_file_is_consistent():
    bench = json.loads((ROOT / "eval" / "benchmark.json").read_text(encoding="utf-8"))
    ids = [s["id"] for s in bench["sets"]]
    assert len(ids) == len(set(ids))
    for s in bench["sets"]:
        for p in s["pages"]:
            assert (ROOT / p).is_file(), p
        dims = s["dimensions"]
        assert isinstance(dims, list) or dims.startswith("same as "), s["id"]
        assert all(v["type"] in ("plan", "front", "side", "section", "detail", "iso", "other") for v in s["views"]), s["id"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
