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
