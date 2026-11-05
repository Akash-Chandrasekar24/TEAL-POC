"""Bounded concurrent extraction with ordered results and per-task context."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context


def ordered_map(function, values, *, workers=2):
    items = list(values)
    if len(items) < 2 or workers <= 1:
        return [function(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items)), thread_name_prefix="drawing-page") as pool:
        futures = [pool.submit(copy_context().run, function, item) for item in items]
        try:
            return [future.result() for future in futures]
        except BaseException:
            for future in futures:
                future.cancel()
            raise
