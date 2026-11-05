"""Phase 1 exit check through the REAL job graph (fake model, fake SolidWorks build): needs the project's requirements (langgraph).

  python tests/test_cache_job.py   (or pytest)

- the same drawing read twice: the second job makes 0 model calls, and still stops at the human approval (the cache never skips a pause)
- a human correction makes a real model call
- a reply that does not fit the list format is not kept: a new job reads again
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["CACHE_DIR"] = tempfile.mkdtemp(prefix="cache-test-")

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from app.agent.graph import Deps, build_graph, new_job_state  # noqa: E402
from app.cache import Cache  # noqa: E402
from app.llm.base import LLMResponse  # noqa: E402
from app.llm.cached import CachedClient  # noqa: E402

GOOD = {"list_version": "1", "part_name": "plate", "unit": "mm", "projection": "first", "seen_dimensions": ["120", "80", "10"],
        "items": [{"id": "F1", "feature": "extrude_profile_boss", "printed": "120 x 80 x 10",
                   "params": {"profile": {"shape": "rectangle", "width": 120, "height": 80}, "depth": 10}}]}


class FakeReader:
    provider, model, cache_settings = "fake", "fake-1", {}

    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def generate_json(self, system, parts):
        self.calls += 1
        data = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return LLMResponse(text="{}", data=data, model=self.model, seconds=1.0, usage={"prompt_tokens": 100, "output_tokens": 50})


def fake_build(approved, job_dir):
    return {"report": {"ok": True, "items": [], "failure": None, "final": {}, "gaps": []},
            "finish": {"ok": True, "files": {"sldprt": str(job_dir / "part.SLDPRT")}, "checks": []}}


def start(graph, image, jid):
    jd = Path(tempfile.mkdtemp(prefix="job-"))
    cfg = {"configurable": {"thread_id": jid}, "recursion_limit": 80}
    out = graph.invoke(new_job_state(jid, str(jd), image), cfg)
    return out, cfg


def drawing() -> str:
    from PIL import Image
    p = Path(tempfile.mkdtemp()) / "drawing.png"
    Image.new("RGB", (40, 30), "white").save(p, "PNG")             # a real PNG: the reader reads the file type from the bytes
    return str(p)


def test_second_job_on_same_drawing_makes_no_model_call_and_still_pauses():
    fake = FakeReader([GOOD])
    graph = build_graph(Deps(CachedClient(fake, Cache(tempfile.mkdtemp())), fake_build), MemorySaver())
    image = drawing()
    out1, cfg1 = start(graph, image, "job1")
    assert fake.calls == 1 and out1["__interrupt__"][0].value["kind"] == "approve_list"
    out2, cfg2 = start(graph, image, "job2")
    assert fake.calls == 1                                                     # answered from the cache
    assert out2["__interrupt__"][0].value["kind"] == "approve_list"            # ...and the human still approves
    assert graph.get_state(cfg2).values["tokens"]["cached"] == 1
    out = graph.invoke(Command(resume={"action": "correct", "message": "the plate is 12 thick"}), cfg2)
    assert fake.calls == 2                                                     # a correction is a new question: a real call
    out = graph.invoke(Command(resume={"action": "approve"}), cfg2)
    assert out["__interrupt__"][0].value["kind"] == "review"                   # build ran, the review pause still comes


def test_invalid_reply_is_not_kept():
    fake = FakeReader([{"items": "not a list"}, GOOD])                         # first reply fails the format, the re-ask fits
    graph = build_graph(Deps(CachedClient(fake, Cache(tempfile.mkdtemp())), fake_build), MemorySaver())
    image = drawing()
    start(graph, image, "a")
    assert fake.calls == 2
    fake.replies = [GOOD]
    start(graph, image, "b")
    assert fake.calls == 3                                                     # the bad first reply was discarded: read again
    start(graph, image, "c")
    assert fake.calls == 3                                                     # the good reply is cached now


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
