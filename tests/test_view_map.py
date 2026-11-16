"""Which part each view shows: name matching misses section views and unlabelled views (a 4-cylinder engine sheet: the connecting rod got
only a small bolting view and its list was written from the cap's sizes). One AI answer, saved, adds them; quantities the sheet does not print
come from the assembly view; the unlabelled isometric is the assembly view."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import fakes  # noqa: F401
from app.web import parts as P

RID = "0d1d89db4ece"


def sheet():
    views = [("01. PISTON", "front"), ("SECTION A-A", "section"), ("", "front"), ("04. CONNECTING ROD", "plan"), ("", "iso")]
    return {"id": RID, "pages": [{"no": 1, "boxes": [[0, 0, 10, 10]] * len(views),
                                  "read": {"views": [{"no": i + 1, "label": l, "type": t} for i, (l, t) in enumerate(views)]}}]}


def test_the_ai_view_map_adds_section_and_unlabelled_views_and_quantities():
    reply = {"views": [{"view": "1.1", "part": "Piston"}, {"view": "1.2", "part": "Piston"}, {"view": "1.3", "part": "connecting rod"},
                       {"view": "1.4", "part": "Connecting Rod"}, {"view": "1.5", "part": "assembly"}, {"view": "1.9", "part": "Flywheel"}],
             "quantities": [{"part": "Piston", "quantity": 4}, {"part": "Connecting Rod", "quantity": 4}, {"part": "Ghost", "quantity": 2}]}
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "views.json"
        with patch.object(P, "view_map_path", lambda rid: path):
            assert [len(p["views"]) for p in P.detect_parts(sheet())] == [1, 1]          # names only: the section and the main rod view are lost
            saved = P.parse_view_map(reply, P.detect_parts(sheet()))
            assert saved["views"]["1.5"] == "assembly" and "1.9" not in saved["views"] and saved["quantities"] == {"piston": 4, "connecting rod": 4}
            path.write_text(json.dumps(saved))
            parts = {p["id"]: p for p in P.detect_parts(sheet())}
            assert [v["no"] for v in parts["piston"]["views"]] == [1, 2] and sorted(v["no"] for v in parts["connecting-rod"]["views"]) == [3, 4]
            assert parts["piston"]["quantity"] == 4
            assert [v["no"] for v in P.assembly_views(sheet())] == [5]                   # the unlabelled isometric, not the piston's section


def test_a_parts_picture_blanks_the_other_parts_views_inside_its_crop():
    """A crankshaft's box took in the piston pin's dimensions and the agent added a pin-sized shaft to the crankshaft."""
    import io
    from PIL import Image
    page = Image.new("RGB", (200, 100), "black")
    buf = io.BytesIO(); page.save(buf, "PNG")
    result = {"pages": [{"no": 1, "boxes": [[0, 0, 150, 100], [120, 60, 200, 100]], "read": {"views": [{"no": 1}, {"no": 2}]}}]}
    part = {"name": "crankshaft", "views": [{"page": 1, "no": 1, "label": "03. CRANKSHAFT", "box": [0, 0, 150, 100]}]}
    sheet = Image.open(io.BytesIO(P.part_sheet(result, part, lambda no: buf.getvalue()))).convert("L")
    w, h = sheet.size
    assert sheet.getpixel((int(w * 0.2), int(h * 0.3))) < 50           # the crankshaft's own drawing is kept
    assert sheet.getpixel((int(w * 0.9), int(h * 0.9))) > 200          # the pin's box inside the crop is blanked
