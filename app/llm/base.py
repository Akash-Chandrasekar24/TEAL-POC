"""The small interface between the orchestrator and any LLM. Swap providers by writing another class.

The LLM only ever returns JSON. It never calls tools.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, Union


@dataclass(frozen=True)
class FilePart:
    """An image or PDF page sent to the model (used by the drawing-reader step)."""
    data: bytes
    mime_type: str  # e.g. "image/png", "application/pdf"


Part = Union[str, FilePart]


@dataclass
class LLMResponse:
    text: str                 # raw reply
    data: Any                 # the reply parsed as JSON
    model: str
    seconds: float
    usage: dict[str, int] = field(default_factory=dict)


class LLMError(RuntimeError):
    """The provider call failed (network, quota, blocked, empty reply)."""


class LLMJsonError(LLMError):
    """The reply was not valid JSON. .raw has the text."""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


class LLMClient(Protocol):
    model: str

    def generate_json(self, system: str, parts: Sequence[Part]) -> LLMResponse:
        """Send system prompt + parts, return the parsed JSON reply. Blocking."""
        ...


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def parse_json_text(text: str) -> Any:
    """json.loads that also tolerates a ```json fence around the reply."""
    body = text.strip()
    m = _FENCE.match(body)
    if m:
        body = m.group(1)
    try:
        return json.loads(body)
    except json.JSONDecodeError as ex:
        raise LLMJsonError(f"reply is not valid JSON: {ex}", text) from ex
