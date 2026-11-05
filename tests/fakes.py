"""Shared fakes for offline tests: a scripted model, a scripted SolidWorks build, and a valid build list. No network, no SolidWorks."""
from __future__ import annotations

import copy
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("CACHE_DIR", tempfile.mkdtemp(prefix="cache-test-"))   # never touch the project's storage/

from app.agent.prompts import reader_system, repair_system  # noqa: E402
from app.llm.base import LLMError, LLMResponse  # noqa: E402

PLATE = {"id": "F1", "feature": "extrude_profile_boss", "printed": "120 x 80 x 10",
         "params": {"profile": {"shape": "rectangle", "width": 120, "height": 80}, "depth": 10}}
HOLE = {"id": "F2", "feature": "hole", "printed": "Ø8 thru",
        "params": {"positions": [{"x": 15, "x_from": "left_edge", "y": 15, "y_from": "bottom_edge"}], "diameter": 8, "face": "front",
                   "depth": "through", "material_length": 10}}
GOOD = {"list_version": "1", "part_name": "plate", "unit": "mm", "projection": "first", "seen_dimensions": ["120", "80", "10", "15", "Ø8"],
        "items": [PLATE, HOLE]}


def good(**changes) -> dict:
    out = copy.deepcopy(GOOD)
    out.update(changes)
    return out


class FakeModel:
    """Answers the reader and the repair step from two scripts. A script entry that is an Exception is raised. The last entry repeats."""
    provider, model, cache_settings = "fake", "fake-1", {}

    def __init__(self, reads=(GOOD,), repairs=()):
        self.reads, self.repairs = list(reads), list(repairs)
        self.calls: list[tuple[str, list]] = []          # (reader | repair, parts)

    def generate_json(self, system, parts):
        kind = "reader" if system == reader_system() else "repair" if system == repair_system() else "other"
        self.calls.append((kind, list(parts)))
        script = self.repairs if kind == "repair" else self.reads           # any other prompt (e.g. view labelling) answers from `reads`
        step = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(step, Exception):
            raise step
        return LLMResponse(text="{}", data=copy.deepcopy(step), model=self.model, seconds=1.0, usage={"prompt_tokens": 100, "output_tokens": 20})

    def count(self, kind: str) -> int:
        return sum(1 for k, _ in self.calls if k == kind)


def failure(item_id="F2", kind="check", feature="hole", message="volume removed 0 mm3, expected 502.65 mm3") -> dict:
    return {"item_id": item_id, "feature": feature, "kind": kind, "message": message, "code": "", "detail": []}


class FakeBuild:
    """Scripted SolidWorks builds: each entry is "ok" or a failure dict. Records the approved lists it was given."""

    def __init__(self, *outcomes):
        self.outcomes, self.lists = list(outcomes) or ["ok"], []

    def __call__(self, approved, job_dir):
        self.lists.append(copy.deepcopy(approved))
        step = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        items = [{"id": it["id"], "feature": it["feature"], "status": "ok", "verdicts": [], "probes": [], "notes": []} for it in approved["items"]]
        if step == "ok":
            return {"report": {"ok": True, "items": items, "failure": None, "final": {"bbox_mm": [120, 80, 10]}, "gaps": []},
                    "finish": {"ok": True, "files": {"sldprt": str(Path(job_dir) / "part.SLDPRT")}, "checks": []}}
        return {"report": {"ok": False, "items": items, "failure": step, "final": {}, "gaps": []}, "finish": None}


def transient() -> LLMError:
    return LLMError("Fake 503 UNAVAILABLE: overloaded")


def drawing(seed: int = 0) -> str:
    """A real (tiny) PNG: the reader reads the file type from the bytes. Same seed = same bytes (= same cache key)."""
    from PIL import Image
    p = Path(tempfile.mkdtemp(prefix="drawing-")) / "drawing.png"
    Image.new("RGB", (40, 30), (255, 255 - seed % 256, 255)).save(p, "PNG")
    return str(p)
