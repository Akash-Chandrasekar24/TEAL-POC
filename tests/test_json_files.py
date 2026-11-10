"""The batch file is written by the build threads while the page polls it: on Windows an unguarded replace / read fails with
PermissionError (measured: it ended a whole build run). write_json and read_json share one lock."""
import tempfile
import threading
from pathlib import Path

from app.assembly.jobs import read_json, write_json


def test_reads_and_replaces_from_several_threads_never_fail():
    f = Path(tempfile.mkdtemp()) / "batch.json"
    write_json(f, {"n": 0})
    errors, stop = [], threading.Event()

    def reader():
        while not stop.is_set():
            try:
                read_json(f)
            except Exception as ex:                                            # noqa: BLE001
                errors.append(repr(ex))

    readers = [threading.Thread(target=reader) for _ in range(3)]
    for t in readers:
        t.start()
    try:
        for i in range(200):
            write_json(f, {"n": i})
    finally:
        stop.set()
        for t in readers:
            t.join()
    assert errors == [] and read_json(f) == {"n": 199}
