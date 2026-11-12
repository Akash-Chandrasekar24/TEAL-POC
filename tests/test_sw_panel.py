"""The chat inside SolidWorks lists the built parts the agent can edit, newest first; nothing else."""
import json
import tempfile
from pathlib import Path

from app.web.sw_panel import panel_jobs


def test_only_built_parts_waiting_for_review_are_listed():
    with tempfile.TemporaryDirectory() as d:
        def job(jid, **r):
            (Path(d) / "jobs" / jid).mkdir(parents=True)
            (Path(d) / "jobs" / jid / "state.json").write_text(json.dumps({"id": jid, "kind": "part", "receipt": {"files": {}}, **r}))
        job("a" * 12, phase="awaiting_review", draft={"part_name": "clamp"})
        job("b" * 12, phase="awaiting_approval")                         # not built yet
        job("c" * 12, phase="awaiting_review", kind="assembly")          # an assembly: the chat edits parts
        assert [(j["id"], j["name"]) for j in panel_jobs(Path(d))] == [("a" * 12, "clamp")]
