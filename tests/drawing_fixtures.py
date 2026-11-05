"""Small, fully attributed plate/hole/boss fixture for the new planning path."""
import copy
import io

from PIL import Image

from app.drawing.model import reconcile
from app.drawing.planner import PageInterpretation, Proposal


def drawing_result():
    labels = ["120", "80", "10", "Ø8", "15", "15", "Ø30", "20"]
    dims = [{"id": f"P1.d{i}", "text": text, "token": text, "views": [2 if i in (3, 8) else 1], "single_source": False}
            for i, text in enumerate(labels, 1)]
    return {"id": "abcdef123456", "llm": "gemini", "pages": [{"no": 1, "source": "plate.png", "source_page": 1,
             "sha": "a" * 64, "width": 800, "height": 600, "boxes": [[0, 0, 300, 250], [300, 0, 700, 250]],
             "read": {"page": 1, "dims": dims, "page_dims": labels, "notes": [], "calls": 2,
                      "title_block": {"name": "plate", "unit": "mm", "projection": "third"},
                      "views": [{"no": 1, "type": "plan", "box": [0, 0, 300, 250], "label": "PLAN", "dims": [d["text"] for d in dims if d["views"] == [1]]},
                                {"no": 2, "type": "section", "box": [300, 0, 700, 250], "label": "SECTION A-A", "dims": ["10", "20"]}]}}]}


def binding(ref):
    return {"expression": "d", "sources": {"d": ref}}


def zero():
    return {"expression": "0", "sources": {}, "reason": "P1.v1 shows the boss at the plate centrelines"}


def interpretations():
    return [PageInterpretation.model_validate({"features": [
        {"id": "P1.f1", "kind": "plate", "description": "rectangular plate", "views": ["P1.v1", "P1.v2"],
         "dimensions": {"width": ["P1.d1"], "height": ["P1.d2"], "thickness": ["P1.d3"]}},
        {"id": "P1.f2", "kind": "hole", "description": "one plate hole", "views": ["P1.v1", "P1.v2"],
         "dimensions": {"diameter": ["P1.d4"], "x": ["P1.d5"], "y": ["P1.d6"], "material_length": ["P1.d3"]}},
        {"id": "P1.f3", "kind": "boss", "description": "central circular boss", "views": ["P1.v1", "P1.v2"],
         "dimensions": {"diameter": ["P1.d7"], "height": ["P1.d8"]}},
    ]})]


def proposal():
    return Proposal.model_validate({"features": [
        {"id": "F1", "kind": "plate", "description": "rectangular plate", "observations": ["P1.f1"],
         "parameters": {"width": binding("P1.d1"), "height": binding("P1.d2"), "thickness": binding("P1.d3")}},
        {"id": "F2", "kind": "hole", "description": "plain through hole", "observations": ["P1.f2"],
         "x_from": "left_edge", "y_from": "bottom_edge", "through": True,
         "parameters": {"diameter": binding("P1.d4"), "x": binding("P1.d5"), "y": binding("P1.d6"), "material_length": binding("P1.d3")}},
        {"id": "F3", "kind": "boss", "description": "round boss at centrelines", "observations": ["P1.f3"],
         "parameters": {"diameter": binding("P1.d7"), "x": zero(), "y": zero(), "height": binding("P1.d8")}},
    ], "relationships": [{"kind": "symmetric", "features": ["F1", "F3"], "views": ["P1.v1"], "evidence": "drawn centrelines"}]})


def model():
    return reconcile(copy.deepcopy(drawing_result()))


def png():
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buf, "PNG")
    return buf.getvalue()
