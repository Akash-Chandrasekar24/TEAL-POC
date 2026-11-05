"""Offline checks of the process log (app.trace): python tests/test_trace.py"""
import io
import json
import tempfile
from pathlib import Path

import httpx
from PIL import Image

import fakes  # noqa: F401
from fakes import FakeModel

from app import config, trace
from app.ingest import load_pages
from app.llm.domo import DomoClient
from app.llm.traced import TracedClient
from app.perception.extract import read_page
from app.perception.segment import segment_page


def events(folder: Path) -> list[dict]:
    return [json.loads(line) for line in (folder / "trace.jsonl").read_text(encoding="utf-8").splitlines()]


def png(w=600, h=400) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "PNG")
    return buf.getvalue()


def test_a_read_is_logged_step_by_step_with_what_the_model_saw_and_said():
    folder = Path(tempfile.mkdtemp()) / "run"
    picture = Path(tempfile.mkdtemp()) / "drawing.jpg"
    picture.write_bytes(png())
    model = TracedClient(FakeModel(reads=[{"d": ["60", "Ø50"], "tb": {"unit": "mm"}, "notes": []},
                                          {"c": {"1": {"t": "plan", "d": ["Ø50"]}, "7": {"t": "iso"}}}]))
    with trace.run("r1", folder, files=["drawing.jpg"]):
        page = load_pages([picture])[0]
        segment_page(page.png, page.sha)
        read_page(model, 1, page.png, [[10, 10, 200, 200], [300, 10, 500, 200]])
    ev = events(folder)
    steps = [(e["stage"], e["event"]) for e in ev]
    for want in [("run", "run start"), ("ingest", "file done"), ("segment", "page done"), ("llm", "call"), ("llm", "reply"),
                 ("pack", "crops packed"), ("read", "crop"), ("combine", "page dimensions"), ("run", "run done")]:
        assert want in steps, (want, steps)
    ingest = next(e for e in ev if e["event"] == "file start")
    assert ingest["type"] == "PNG" and ingest["extension"] == ".jpg"                # the real type, not the extension
    assert next(e for e in ev if e["event"] == "file done")["pages"] == 1
    warns = [e["event"] for e in ev if e["level"] == "warn"]
    assert "reply names crops that are not on this sheet (ignored)" in warns           # crop 7 does not exist
    assert "crop missing from the reply (no name, no dimensions)" in warns             # crop 2 was not answered
    calls = [e for e in ev if e["event"] == "call"]
    assert len(calls) == 2 and all((folder / "files" / p["file"]).is_file() for c in calls for p in c["parts"] if "file" in p)
    replies = [e for e in ev if e["event"] == "reply"]
    assert all((folder / "files" / r["reply_file"]).is_file() for r in replies)
    assert "run.log" in [p.name for p in folder.iterdir()]


def test_a_failure_is_logged_with_its_traceback():
    folder = Path(tempfile.mkdtemp()) / "run"
    try:
        with trace.run("r2", folder):
            with trace.current().step("read", "page"):
                raise RuntimeError("boom")
    except RuntimeError:
        pass
    failed = [e for e in events(folder) if e["level"] == "error"]
    assert [e["event"] for e in failed] == ["page failed", "run failed"] and "boom" in failed[0]["error"] and "Traceback" in failed[0]["traceback"]


def test_outside_a_run_nothing_is_written():
    assert trace.current() is trace.NO_TRACE
    with trace.current().step("x", "y") as out:
        out["a"] = 1
    assert trace.current().save("f", b"data") is None


def test_the_token_never_reaches_the_log():
    folder = Path(tempfile.mkdtemp()) / "run"
    config.DOMO_INSTANCE = "acme"
    domo = TracedClient(DomoClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"output": '{"d": []}'}))))
    big = png(3000, 1000)                                                              # over Domo's 2048 px: the shrink is logged too
    from app.llm.base import FilePart
    with trace.run("r3", folder):
        domo.generate_json("SYS", [FilePart(big, "image/png")])
    text = (folder / "trace.jsonl").read_text(encoding="utf-8") + (folder / "run.log").read_text(encoding="utf-8")
    assert "secret-token" not in text and config.domo_token() not in text
    assert any(e["event"].startswith("picture shrunk for Domo") for e in events(folder))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
