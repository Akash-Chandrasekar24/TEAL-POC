"""One native CAD writer, with an intent record before any external side effect."""
from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app import config
from .harness import HarnessStopped


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _alive(pid) -> bool:
    """True while that process exists (unknown -> True: never take a lock from a process that may still run)."""
    if not isinstance(pid, int) or pid <= 0:
        return True
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259                                               # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@contextmanager
def cad_execution(job_dir: Path, operation: str, plan_hash: str, *, lock_path: Path | None = None):
    """No replay of an uncertain native action. The lock of a process that no longer exists is released (a server stopped mid-build left
    it, and every later build stopped on "already reserved"); that job's own intent record still says what was interrupted."""
    lock = lock_path if lock_path is not None else config.PROJECT_ROOT / "storage/solidworks-execution.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    owner = uuid.uuid4().hex
    record = {"owner": owner, "pid": os.getpid(), "operation": operation, "plan_hash": plan_hash,
              "job_dir": str(Path(job_dir).resolve()), "started_at": datetime.now(timezone.utc).isoformat()}
    for attempt in (1, 2):
        try:
            with lock.open("x", encoding="utf-8") as file:
                json.dump(record, file)
            break
        except FileExistsError:
            try:
                holder = json.loads(lock.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                holder = {}
            if attempt == 2 or _alive(holder.get("pid")):
                raise HarnessStopped("SolidWorks execution is already reserved; inspect the active job before another build") from None
            lock.unlink(missing_ok=True)
    intent = Path(job_dir) / "harness" / "native-intent.json"
    try:
        if intent.is_file():
            try:
                previous = json.loads(intent.read_text(encoding="utf-8"))
                if not isinstance(previous, dict):
                    raise ValueError("Invalid intent")
            except (ValueError, OSError):
                raise HarnessStopped("The native execution intent is unreadable; inspect it before retrying") from None
            if previous.get("status") in ("running", "needs_review"):
                raise HarnessStopped("An earlier native execution needs reconciliation; inspect its intent record and SolidWorks state before retrying")
        _write(intent, {**record, "status": "running"})
        try:
            yield
        except BaseException:
            _write(intent, {**record, "status": "needs_review"})
            raise
        else:
            _write(intent, {**record, "status": "completed"})
    finally:
        if lock.is_file() and json.loads(lock.read_text(encoding="utf-8")).get("owner") == owner:
            lock.unlink()
