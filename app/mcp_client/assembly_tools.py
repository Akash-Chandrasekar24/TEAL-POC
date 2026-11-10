"""Register application-owned assembly verification alongside upstream tools.

Loaded inside the adapter host. Importing this module never starts execution.
The HTTP call attaches to an already-running SolidWorks instance only.
"""
from typing import Literal


def register(mcp):
    @mcp.tool()
    def inspect_drawing() -> dict:
        """Read the active native drawing's model references, scales, bounds and dimensions; no COM startup."""
        from execution_client import _http_client
        from config import EXECUTION_BASE_URL
        with _http_client(timeout=120) as client:
            response = client.get(EXECUTION_BASE_URL + "/api/drawing/inspect")
            response.raise_for_status()
            return response.json()

    def geometry(path: str, spec: dict, timeout: float) -> str:
        """POST to an application geometry endpoint; 'FAILED | reason' when SolidWorks refuses (the client treats that as a tool failure)."""
        import json
        from execution_client import _http_client
        from config import EXECUTION_BASE_URL
        with _http_client(timeout=timeout) as client:
            response = client.post(EXECUTION_BASE_URL + path, json=spec)
            if response.status_code == 409:
                return "FAILED | " + str(response.json().get("error", response.text))[:800]
            response.raise_for_status()
            return json.dumps(response.json())

    @mcp.tool()
    def ext_command_in_progress(on: bool) -> str:
        """Tell SolidWorks a program is driving it (on=true: COM calls ~30x faster; the SolidWorks window waits). Always switch it off after."""
        return geometry("/api/ext/command_in_progress", {"on": bool(on)}, 60)

    @mcp.tool()
    def ext_bbox() -> str:
        """Exact world bounding box (metres) of the active part's solid bodies: {lo, hi, bodies}."""
        return geometry("/api/ext/bbox", {}, 120)

    @mcp.tool()
    def ext_clear_selection() -> str:
        """Clear the selection in the active document (before a save / export: an STL is otherwise written for the selected body only)."""
        return geometry("/api/ext/clear_selection", {}, 60)

    @mcp.tool()
    def ext_sweep(spec: dict) -> str:
        """Sweep a profile sketch along a path sketch, as a boss or a CUT. spec: {operation: boss|cut, path: {plane, entities}, profile: {plane, entities}};
        plane = {datum: front|top|right, offset?} or {name, offset?}; entities in MODEL coordinates (metres): line {p1,p2}, arc {center,p1,p2,mid}, circle {center,radius}."""
        return geometry("/api/ext/sweep", spec, 240)

    @mcp.tool()
    def ext_shell(spec: dict) -> str:
        """Shell the active part: spec {thickness (m), faces: [[x,y,z] a point on each face to remove], outward?: false}."""
        return geometry("/api/ext/shell", spec, 240)

    @mcp.tool()
    def ext_angled_plane(spec: dict) -> str:
        """A plane at an angle to a reference plane, through an axis: spec {reference: plane, point: [x,y,z], along: x|y|z, angle_deg, normal: [x,y,z]}.
        The axis runs through `point` along `along`; the plane comes out with +normal along `normal` (rebuilt with the flip option if needed)."""
        return geometry("/api/ext/angled_plane", spec, 240)

    @mcp.tool()
    def ext_sketch_extrude(spec: dict) -> str:
        """Sketch on any plane and extrude: spec {operation: boss|cut, plane, entities (model coordinates), direction: [x,y,z] the way it grows,
        end: blind|through_all, depth (m), depth_back? (m, also extrude the other way)}."""
        return geometry("/api/ext/sketch_extrude", spec, 240)

    @mcp.tool()
    def inspect_assembly(inspection_type: Literal["workspace", "verification"] = "verification") -> dict:
        """Inspect open documents, or rebuild/check bounds, constraints and interference of the active assembly."""
        from execution_client import _http_client
        from config import EXECUTION_BASE_URL
        with _http_client(timeout=180) as client:
            response = client.get(EXECUTION_BASE_URL + "/api/assembly/inspect",
                                  params={"workspace": str(inspection_type == "workspace").lower()})
            response.raise_for_status()
            return response.json()
