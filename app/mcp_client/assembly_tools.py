"""Register application-owned assembly verification alongside upstream tools.

Loaded inside the adapter host. Importing this module never starts execution.
The HTTP call attaches to an already-running SolidWorks instance only.
"""
from typing import Literal


def _local_http_without_tls() -> None:
    """Every tool call makes a new httpx client, and each one built an SSL context: measured 0.65-0.87 s per call (a trivial call took 1 s, the
    clamp build 314 s instead of 180 s). The execution server is plain http on this machine, so no TLS is needed: 0.003 s per client."""
    import execution_client
    if getattr(execution_client._http_client, "_no_tls", False):
        return
    original = execution_client._http_client

    def client(**kwargs):
        kwargs.setdefault("verify", False)
        return original(**kwargs)
    client._no_tls = True
    execution_client._http_client = client


def register(mcp):
    _local_http_without_tls()

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
    def ext_drill(spec: dict) -> str:
        """A hole along ANY direction: spec {point: [x,y,z] entry (m), direction: [x,y,z] into the material, cuts: [{radius (m), end: blind|through_all,
        depth (m)}]}: the hole first, then any counterbore over it."""
        return geometry("/api/ext/drill", spec, 240)

    @mcp.tool()
    def ext_feature_count() -> str:
        """How many features the active part has (to undo one build step exactly with ext_delete_after)."""
        return geometry("/api/ext/feature_count", {}, 60)

    @mcp.tool()
    def ext_delete_after(count: int) -> str:
        """Delete every feature after the first `count` (and the sketches they absorbed): undoes the steps made since ext_feature_count."""
        return geometry("/api/ext/delete_after", {"count": int(count)}, 120)

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
    def ext_annotate(spec: dict) -> str:
        """Notes with leaders on faces of the active part: spec {notes: [{point: [x,y,z] m on the face, text}]}. Annotations are switched on."""
        return geometry("/api/ext/annotate", spec, 240)

    @mcp.tool()
    def ext_revolve(spec: dict) -> str:
        """Revolve on any plane: spec {plane, entities (model coordinates; exactly ONE line with construction: true = the axis), angle (radians),
        reverse?}. The axis is the sketch's centreline: nothing is picked by coordinates."""
        return geometry("/api/ext/revolve", spec, 240)

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
