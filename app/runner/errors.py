"""Classify a failed SolidWorks result.

Rule:
  - about the graph's CONTENT        -> goes to the build-fixer
  - about the ENVIRONMENT (document state, SolidWorks busy / not attached) -> code retries once
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Codes that say "the machine/document is in a bad state", not "the graph is wrong".
ENVIRONMENT_CODES = frozenset({
    "COM_ATTACH_FAILED", "COM_ERROR", "UNEXPECTED_ERROR", "UNEXPECTED", "COMPILER_UNAVAILABLE",
    "NO_ACTIVE_DOCUMENT", "INVALID_STATE_VERSION", "DOCUMENT_CREATION_FAILED", "DOCUMENT_NOT_FOUND",
    "DOCUMENT_CAST_FAILED", "TEMPLATE_NOT_FOUND", "SKETCH_ALREADY_ACTIVE", "ROLLBACK_FAILED",
    "ACTIVATE_FAILED", "OPEN_FAILED", "SAVE_FAILED", "EXPORT_FAILED", "STATE_SEED_FAILED",
    "WRONG_DOCUMENT_TYPE",
})
_ENV_TEXT = re.compile(r"not ready|timed out|timeout|connection|cannot connect|solidworks is not|com attach|server is down", re.I)
_FAILED = re.compile(r"^\s*FAILED\s*\|\s*([A-Z0-9_]+)\s*\|\s*(.*)$", re.S)
_CAD_FAILED = re.compile(r"CAD operation failed\s*\|\s*code=([A-Z0-9_]+)\s*\|\s*message=(.*)$", re.S)


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str
    kind: str   # "graph" | "environment"

    @property
    def is_environment(self) -> bool:
        return self.kind == "environment"


def classify_failure(text: str, *, transport_error: bool = False) -> ErrorInfo:
    """Turn a failed tool text (or a transport/timeout problem) into ErrorInfo."""
    if transport_error:
        return ErrorInfo("TRANSPORT", text[:500], "environment")
    m = _FAILED.match(text) or _CAD_FAILED.search(text)
    if not m:
        kind = "environment" if _ENV_TEXT.search(text) else "graph"
        return ErrorInfo("UNKNOWN", text[:500], kind)
    code, message = m.group(1), m.group(2).strip()
    kind = "environment" if code in ENVIRONMENT_CODES or _ENV_TEXT.search(message) else "graph"
    return ErrorInfo(code, message, kind)
