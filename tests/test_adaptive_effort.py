"""Simple pages (by local OCR line count) are read at a lower Domo effort; dense and near-empty pages are not."""
import json
from unittest.mock import patch

import fakes  # noqa: F401
from app import config
from app.llm.domo import DomoClient, effort_override
from app.perception.hybrid import page_effort
from test_llm_domo import fake, png, USAGE  # reuse the fake Domo transport
from app.llm.base import FilePart


def ocr(n):
    return {"lines": [{"id": f"l{i}"} for i in range(n)]}


def test_page_effort_thresholds():
    assert [page_effort(ocr(n)) for n in (8, 10, 17, 20)] == ["none"] * 4
    assert page_effort(ocr(25)) is None                       # dense clamp
    assert page_effort(ocr(0)) is None and page_effort(ocr(7)) is None   # little OCR evidence: keep the normal effort
    with patch.object(config, "EXTRACTION_SIMPLE_MAX_OCR_LINES", 0):
        assert page_effort(ocr(10)) is None                   # switched off


def test_override_changes_request_and_cache_settings_only_inside_the_block():
    transport, seen = fake({"output": json.dumps({"a": 1}), "modelProviderUsage": USAGE, "modelId": "m1"})
    client = DomoClient(transport=transport, reasoning_effort="medium")
    with effort_override("none"):
        assert client.cache_settings == {"reasoning_effort": "none"}
        client.generate_json("S", [FilePart(png(100), "image/png")])
    client.generate_json("S", [FilePart(png(100), "image/png")])
    with effort_override(None):
        assert client.cache_settings == {"reasoning_effort": "medium"}
    assert [r["body"]["reasoningConfig"]["reasoningEffort"] for r in seen] == ["NONE", "MEDIUM"]


def test_read_hybrid_applies_the_override_only_for_simple_pages():
    from app.agent.workers import ExtractionAgent
    from app.llm import domo
    seen = []
    class Fake:
        model = "m"; provider = "domo"
    with patch("app.perception.hybrid.extract", lambda *a, **k: seen.append(domo._EFFORT.get())):
        agent = ExtractionAgent(Fake())
        agent.read_hybrid(1, b"", ocr(10), "r1")
        agent.read_hybrid(1, b"", ocr(25), "r1")
    assert seen == ["none", None] and domo._EFFORT.get() is None     # simple -> none, dense -> unchanged, and nothing leaks
