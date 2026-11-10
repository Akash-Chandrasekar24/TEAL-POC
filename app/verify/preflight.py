"""Pre-run check: only the expected documents are open in SolidWorks.

The MCP adapter has no "list open documents" tool (verify_state only names the ACTIVE one), so this check reads the
open-document list through COM, READ-ONLY. It is the only place outside the MCP where we touch SolidWorks; it changes
nothing. Closing leftover scratch parts goes through the MCP (activate_document + close_document).
"""
from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Iterable

SCRATCH = re.compile(r"^Part\d+$")      # untitled parts SolidWorks creates; never a user's work


class PreflightError(RuntimeError):
    pass


@dataclass
class OpenDoc:
    title: str
    path: str

    @property
    def is_scratch(self) -> bool:
        """An untitled Part1, or a part an earlier build job of this app saved in its own job folder (a failed job can leave one open;
        it must not block every later part). A saved file anywhere else may be someone's work: never closed."""
        if not self.path:
            return bool(SCRATCH.match(self.title))
        from app import config
        try:
            return Path(self.path).resolve().is_relative_to((config.PROJECT_ROOT / "storage" / "workflow" / "jobs").resolve())
        except (OSError, ValueError):
            return False


@dataclass
class PreflightReport:
    open_docs: list[OpenDoc]
    unexpected: list[OpenDoc] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (f"open: {[d.title for d in self.open_docs]} | unexpected: {[d.title for d in self.unexpected]} | "
                f"closed: {self.closed}")


def list_open_documents() -> list[OpenDoc]:
    """Titles and saved paths of every open document (COM, read-only). Builds run on worker threads, so COM is initialised here, per call."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:
        app = win32com.client.Dispatch("SldWorks.Application")
        docs, d = [], app.GetFirstDocument
        while d is not None:
            docs.append(OpenDoc(str(d.GetTitle), str(d.GetPathName or "")))
            d = d.GetNext
        return docs
    finally:
        pythoncom.CoUninitialize()


def split_unexpected(docs: Iterable[OpenDoc], expected: Iterable[str] = ()) -> list[OpenDoc]:
    """Documents whose title is neither in `expected` nor matches an expected regex-free name (compared without extension)."""
    names = {e.lower().rsplit(".", 1)[0] if e.lower().endswith((".sldprt", ".sldasm", ".slddrw")) else e.lower() for e in expected}
    return [d for d in docs if d.title.lower().rsplit(".", 1)[0] not in names and d.title.lower() not in names]


async def ensure_clean_workspace(sw, expected: Iterable[str] = (), close_scratch: bool = True) -> PreflightReport:
    """Raise PreflightError unless only `expected` documents are open.
    Leftover UNSAVED untitled parts (Part1, Part2 ...) are closed when close_scratch=True; anything else (a saved file,
    an assembly, a drawing) is never closed: it raises, because it may be someone's work."""
    docs = list_open_documents()
    report = PreflightReport(open_docs=docs, unexpected=split_unexpected(docs, expected))
    for d in list(report.unexpected):
        if close_scratch and d.is_scratch:
            await sw.activate_document(d.title)
            r = await sw.close_document(save=False)
            if not r.ok:
                raise PreflightError(f"could not close {d.title}: {r.text[:200]}")
            report.closed.append(d.title)
            report.unexpected.remove(d)
    if report.unexpected:
        raise PreflightError("unexpected documents are open: " + ", ".join(f"{d.title} ({d.path or 'unsaved'})" for d in report.unexpected)
                             + ". Close them or list them as expected.")
    return report
