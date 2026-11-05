"""Simulated two-block assembly; these bytes are NOT valid CAD reference models."""
import copy
import json
from pathlib import Path

from app.assembly.assets import import_model
from app.assembly.model import AssemblyPlan
from app.mcp_client.solidworks import ToolResult


def ready_plan(assets):
    asset = import_model("block.SLDPRT", bytes.fromhex("d0cf11e0a1b11ae1") + b"FAKE CAD TEST ONLY", assets)
    return AssemblyPlan.model_validate({"name": "two_blocks", "source_hash": "test-drawing", "unit": "mm", "unit_confirmed": True, "bom_complete": True,
        "bom": [{"id": "B1", "part_number": "BLOCK-10", "description": "10 mm test block", "quantity": 2,
                 "kind": "part", "evidence": [{"page": 1, "text": "BLOCK-10 QTY 2"}], "confirmed": True}],
        "models": [{"bom_id": "B1", "sha256": asset["sha256"], "source_name": asset["source_name"],
                    "configuration": "Default", "provenance": "TEST ONLY", "confirmed": True}],
        "occurrences": [{"id": "base", "bom_id": "B1", "fixed": True,
                         "placement": {"translation": [0, 0, 0], "evidence": "test origin", "confirmed": True}},
                        {"id": "block", "bom_id": "B1", "placement": {"translation": [10, 0, 0], "evidence": "test X=10", "confirmed": True}}],
        "mates": [{"id": "M1", "type": "lock", "evidence": "test rigid joint", "confirmed": True,
                   "a": {"occurrence": "base", "kind": "plane", "point": [10, 0, 0], "direction": [1, 0, 0]},
                   "b": {"occurrence": "block", "kind": "plane", "point": [0, 0, 0], "direction": [1, 0, 0]}}],
        "expected": {"size": [20, 10, 10], "size_evidence": "test drawing envelope"}})


class FakeAssembly:
    def __init__(self, *, drift=False, interference=False, constraints=3, extra_docs=False, ambiguous=False, bad_config=False):
        self.calls, self.components, self.mates = [], [], []
        self.title, self.path = "", ""
        self.drift, self.interference, self.constraints = drift, interference, constraints
        self.extra_docs, self.ambiguous, self.bad_config = extra_docs, ambiguous, bad_config

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            features, data = [], None
            if name == "open_new_assembly":
                self.title = "Assembly1"
            elif name == "inspect_assembly":
                data = {"title": self.title, "documents": [{"title": self.title, "path": self.path}] if self.title else []}
                if self.extra_docs:
                    data["documents"].append({"title": "UserDocument", "path": "C:/user.sldprt"})
                if args[0] == "verification":
                    data.update(rebuild_ok=True, feature_errors=[], bounds_m=[0, 0, 0, .02, .01, .01], interference_complete=True,
                                interferences=[{"volume_m3": .000001}] if self.interference else [],
                                constraints=[{"name": c["name"], "fixed": c["fixed"], "constrained_status": self.constraints} for c in self.components])
            elif name == "insert_component":
                n = f"Block-{len(self.components)+1}"
                self.components.append({"name": n, "path": kwargs["file_path"], "config": "Wrong" if self.bad_config else kwargs["config"],
                                        "fixed": kwargs["fixed"], "transform": json.loads(kwargs["transform_json"])})
                features = [n]
            elif name == "analyze_assembly":
                if args[0] == "components":
                    entries = copy.deepcopy(self.components)
                    if self.drift and self.mates:
                        entries[-1]["transform"][9] += .1
                    payload = {"component_count": len(entries), "components": entries}
                elif args[0] == "mates":
                    payload = {"mate_count": len(self.mates), "mates": self.mates}
                else:
                    point = [.01, 0, 0] if kwargs["component"] == "Block-1" else [0, 0, 0]
                    faces = [{"i": 0, "planar": True, "normal": [1, 0, 0], "point": point}]
                    if self.ambiguous:
                        faces.append({**faces[0], "i": 1})
                    payload = {"component": kwargs["component"], "face_count": len(faces), "faces": faces}
                features = [json.dumps(payload)]
            elif name == "add_mate":
                n = f"Mate{len(self.mates)+1}"
                self.mates.append({"name": n, "type": kwargs["mate_type"], "alignment": kwargs["alignment"],
                                   "value": kwargs["value"], "entities": [{"component": kwargs["a_component"]}, {"component": kwargs["b_component"]}]})
                features = [n]
            elif name == "save_document":
                path = Path(args[0]); path.write_bytes(b"FAKE ASSEMBLY OUTPUT")
                self.title, self.path = path.name, str(path)
            elif name == "export_document":
                Path(args[1]).write_bytes(b"FAKE STEP OUTPUT")
            else:
                raise AssertionError(f"Unexpected fake call: {name}")
            return ToolResult(name, json.dumps(data) if data is not None else "COMPLETED | features=" + repr(features), False, 0)
        return call
