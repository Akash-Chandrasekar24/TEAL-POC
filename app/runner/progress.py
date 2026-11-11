"""Live progress of one build, for the SolidWorks page: the steps (waiting / building / ok / ...), the step now and the next one, a picture of
the part after each step, and a log that includes the SolidWorks agent's fixes. Written to <job>/progress.json; the page polls it."""
from __future__ import annotations

import threading
import time
from pathlib import Path

LOG_MAX = 300


class Progress:
    def __init__(self, folder: Path):
        from app.assembly.jobs import write_json
        self._write = write_json
        self.folder = Path(folder)
        self._lock = threading.RLock()                     # the live picture is drawn on another thread
        self.data = {"steps": [], "current": None, "log": [], "image": None, "image_step": None,
                     "drawing": (self.folder / "drawing.png").is_file(), "phase": "building", "updated": time.time()}

    def save(self) -> None:
        with self._lock:
            self.data["updated"] = time.time()
            try:
                self._write(self.folder / "progress.json", self.data)
            except Exception:                              # noqa: BLE001 - progress is for the eyes only: never stops a build
                pass

    def plan(self, items) -> None:
        self.data["steps"] = [{"id": i.id, "feature": i.feature, "printed": getattr(i, "printed", "") or "", "status": "waiting", "message": ""} for i in items]
        self.log("plan", f"{len(items)} steps to build")

    def step(self, ids, status: str, message: str = "") -> None:
        for s in self.data["steps"]:
            if s["id"] in ids:
                s["status"], s["message"] = status, message[:300]
        if status == "building":
            self.data["current"] = ids[0] if ids else None
        self.save()

    def add_step(self, item, status: str, message: str = "") -> None:
        self.data["steps"].append({"id": item.id, "feature": item.feature, "printed": "", "status": status, "message": message[:300]})
        self.save()

    def log(self, kind: str, text: str) -> None:
        self.data["log"] = (self.data["log"] + [{"t": round(time.time(), 1), "kind": kind, "text": text[:400]}])[-LOG_MAX:]
        self.save()

    def image(self, name: str, step: str | None) -> None:
        self.data["image"], self.data["image_step"] = name, step
        self.save()

    def finish(self, ok: bool) -> None:
        self.data["phase"], self.data["current"] = ("done" if ok else "stopped"), None
        self.log("done" if ok else "warn", "Build finished" if ok else "Build stopped")
