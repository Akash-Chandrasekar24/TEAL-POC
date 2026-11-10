"""Rehearse the demo on a drawing exactly as a person would click it, against the running app (http://127.0.0.1:8765), with timings.

  python scripts/demo_rehearsal.py inputs/image_9.png [inputs/image_8.jpg ...]   [--no-build]

Per drawing: upload + read -> "Approve all measurements" -> "Build in SolidWorks" (the one button: build lists, AI decides doubts, every part
built, one assembly for several parts). Every step is the same request the page sends. It makes a NEW result per
drawing, so earlier reviews are left alone. The approvals stand in for the human's clicks during a rehearsal only.
"""
from __future__ import annotations

import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8765"
H = {"Content-Type": "application/json", "Origin": BASE}
LOCK = Path(__file__).resolve().parents[1] / "storage" / "solidworks-execution.lock"


def post(path, body, timeout=3600):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers=H)
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as ex:
        raise RuntimeError(f"{path}: HTTP {ex.code}: {ex.read().decode(errors='replace')[:400]}") from None


def get(path):
    return json.load(urllib.request.urlopen(BASE + path, timeout=120))


def wait_batch(rid, phases=("preparing", "building"), every=5):
    while True:
        b = get(f"/api/parts/{rid}")["batch"]
        if b["phase"] not in phases:
            return b
        time.sleep(every)


def rehearse(path: Path, build: bool) -> dict:
    out, t0 = {"drawing": path.name}, time.time()
    stamp = lambda: round(time.time() - t0)
    data = base64.b64encode(path.read_bytes()).decode()
    res = post("/api/read", {"files": [{"name": path.name, "data": data}], "read": True, "workflow": True, "progressive": True, "strategy": "legacy"})
    rid = res["id"]
    out.update(result=rid, read_s=stamp())
    r = get(f"/api/result/{rid}")
    model = (r.get("design") or {}).get("model") or r.get("drawing_model") or {}
    dims = model.get("dimensions", [])
    resolutions = {"dimensions": [{"id": d["id"], "confirmed": True, "text": None} for d in dims], "note": "", "clarifications": []}
    for key in ("unit", "projection"):
        if model.get(key) and model[key] != "unknown":
            resolutions[key] = model[key]
    post("/api/review", {"result_id": rid, "source_hash": model["source_hash"], "resolutions": resolutions})
    out.update(readings=len(dims), approved_s=stamp())
    if not build:
        return out
    post("/api/parts/build-in-solidworks", {"result_id": rid})                  # THE button: one flow, no pause
    b = wait_batch(rid, ("preparing", "resolving", "building", "assembling"), 10)
    out["built"] = {i["name"]: ((i.get("job") or {}).get("phase") or i["status"], ((i.get("job") or {}).get("detail") or i.get("detail") or "")[:200]) for i in b["items"]}
    out["assembly"] = b.get("assembly")
    out["total_s"] = stamp()
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for a in args:
        try:
            print(json.dumps(rehearse(Path(a), "--no-build" not in sys.argv), ensure_ascii=False), flush=True)
        except Exception as ex:                                          # noqa: BLE001 - one drawing's failure does not stop the others
            print(json.dumps({"drawing": Path(a).name, "error": f"{type(ex).__name__}: {ex}"[:600]}), flush=True)
