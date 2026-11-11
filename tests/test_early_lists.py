"""Build lists are written as soon as the measurements are approved: the button finds them ready (or waits for the one being written)."""
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import fakes  # noqa: F401
from app.web import server


def test_missing_lists_start_once_and_the_button_waits_for_them():
    calls, gate = [], threading.Event()

    def write(rid, pid):
        calls.append(pid)
        gate.wait(5)
        return {"id": f"list-{pid}"}
    result = {"drawing_model": {"issues": []}}
    with patch.object(server, "result_with_design", lambda rid: result), patch.object(server, "create_part_list", write), \
         patch.object(server, "_PREP_POOL", ThreadPoolExecutor(2)), patch.dict(server._EARLY, clear=True), \
         patch("app.web.parts.detect_parts", lambda r: [{"id": "a"}, {"id": "b"}]), patch("app.web.parts.latest_lists", lambda *a: {"b": {"id": "old"}}):
        server.start_lists_early("r1")
        server.start_lists_early("r1")                                           # page polled again while it is being written
        gate.set()
        assert server._early_list("r1", "a") == {"id": "list-a"} and calls == ["a"]   # one call, only for the part with no list
        assert server._early_list("r1", "b") is None


def test_nothing_starts_before_the_measurements_are_approved():
    result = {"drawing_model": {"issues": ["P1.d3 (Ø88): human review required"]}}
    with patch.object(server, "result_with_design", lambda rid: result), patch.dict(server._EARLY, clear=True):
        server.start_lists_early("r2")
        assert server._EARLY == {}
