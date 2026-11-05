"""TRACE: a detailed, per-run log for debugging. One folder per run: storage/runs/<run id>/

  trace.jsonl   one JSON event per line: {"t": seconds since the run started, "ts", "stage", "event", "level", ...details}
  run.log       the same events as readable lines
  files/        what the model was SHOWN (every packed crop sheet) and what it ANSWERED (every raw reply), so a missed number can be traced

Code anywhere in a run logs through `current()` (a context variable), so nothing has to pass the tracer around; outside a run `current()` is a
no-op tracer. Threads started inside a run (parallel calls) inherit it when started with `contextvars.copy_context()`; the writes are locked.
Never log secrets: the model clients only hand prompts, sizes, replies and token counts to the trace.
"""
from __future__ import annotations

import contextvars
import json
import re
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _short(value: Any, limit: int = 300) -> Any:
    """Keep run.log readable: long strings / lists are cut there (trace.jsonl keeps everything)."""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"… ({len(value)} chars)"
    if isinstance(value, list) and len(value) > 12:
        return value[:12] + [f"… ({len(value)} items)"]
    return value


class Trace:
    def __init__(self, run_id: str, folder: Path, save_files: bool = True):
        self.run_id, self.folder, self.save_files = run_id, Path(folder), save_files
        (self.folder / "files").mkdir(parents=True, exist_ok=True)
        self._t0 = time.monotonic()
        self._lock = threading.Lock()
        self._n = 0

    def event(self, stage: str, event: str, level: str = "info", **data: Any) -> None:
        t = round(time.monotonic() - self._t0, 3)
        row = {"t": t, "ts": datetime.now().isoformat(timespec="milliseconds"), "stage": stage, "event": event, "level": level, **data}
        line = json.dumps(row, ensure_ascii=False, default=str)
        detail = " ".join(f"{k}={json.dumps(_short(v), ensure_ascii=False, default=str)}" for k, v in data.items())
        with self._lock:
            with open(self.folder / "trace.jsonl", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            with open(self.folder / "run.log", "a", encoding="utf-8") as fh:
                fh.write(f"{t:8.3f}s {level.upper():5} {stage:8} {event}  {detail}\n")

    def warn(self, stage: str, event: str, **data: Any) -> None:
        self.event(stage, event, level="warn", **data)

    @contextmanager
    def step(self, stage: str, event: str, **data: Any) -> Iterator[dict]:
        """Logs `<event> start`, then `<event> done` with the seconds taken and whatever the block put in the yielded dict, or
        `<event> failed` with the error and its traceback (and re-raises)."""
        out: dict = {}
        t0 = time.monotonic()
        self.event(stage, f"{event} start", **data)
        try:
            yield out
        except BaseException as ex:
            self.event(stage, f"{event} failed", level="error", seconds=round(time.monotonic() - t0, 3), error=f"{type(ex).__name__}: {ex}",
                       traceback=traceback.format_exc(limit=12))
            raise
        self.event(stage, f"{event} done", seconds=round(time.monotonic() - t0, 3), **out)

    def save(self, name: str, data: bytes | str) -> str | None:
        """Keep a file of the run (a picture sent to the model, a raw reply). -> its name inside files/, numbered in order."""
        if not self.save_files:
            return None
        with self._lock:
            self._n += 1
            fname = f"{self._n:03d}_{_SAFE.sub('_', name)[:80]}"
        path = self.folder / "files" / fname
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        return fname


class _NoTrace(Trace):
    """Outside a run: every call does nothing."""

    def __init__(self):                                                      # no folder, no files
        self.run_id, self.save_files = "", False

    def event(self, *a, **k) -> None:
        pass

    @contextmanager
    def step(self, *a, **k) -> Iterator[dict]:
        yield {}

    def save(self, *a, **k) -> None:
        return None


NO_TRACE = _NoTrace()
_CURRENT: contextvars.ContextVar[Trace] = contextvars.ContextVar("trace", default=NO_TRACE)


def current() -> Trace:
    return _CURRENT.get()


@contextmanager
def run(run_id: str, folder: Path, save_files: bool = True, **data: Any) -> Iterator[Trace]:
    """A traced run: everything logged inside goes to folder/. Logs the run's start, end and any error."""
    tr = Trace(run_id, folder, save_files)
    token = _CURRENT.set(tr)
    try:
        with tr.step("run", "run", run_id=run_id, **data):
            yield tr
    finally:
        _CURRENT.reset(token)
