"""Optional Node-based UI behavior smoke: python tests/check_drawing_ui.py."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import fakes  # noqa: F401
from drawing_fixtures import drawing_result, interpretations, model, proposal

from app.drawing.model import Resolutions, reconcile
from app.drawing.planner import compile_plan
from assembly_fixtures import ready_plan


def main():
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Node.js is required for this optional UI smoke test")
    result, drawing = drawing_result(), model()
    original = {"id": "123456abcdef", "model": drawing.model_dump(), "seconds": 1,
                "plan": compile_plan(drawing, interpretations(), proposal())}
    original["plan"]["calls"] = 2
    result.update(drawing_model=drawing.model_dump(), design=original)
    corrected = reconcile(result, Resolutions.model_validate({"dimensions": [{"id": "P1.d1", "text": "125"}]}))
    response = {"id": "fedcba654321", "model": corrected.model_dump(), "seconds": 1,
                "plan": compile_plan(corrected, interpretations(), proposal())}
    response["plan"]["calls"] = 2
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="drawing-ui-") as directory:
        fixture = Path(directory) / "fixture.json"
        assembly = ready_plan(Path(directory) / "assets").model_dump(mode="json")
        fixture.write_text(json.dumps({"result": result, "response": response, "assembly": assembly}), encoding="utf-8")
        subprocess.run([node, "tests/check_drawing_ui.cjs", str(fixture)], cwd=root, check=True)


if __name__ == "__main__":
    main()
