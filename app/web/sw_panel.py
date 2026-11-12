"""The chat INSIDE SolidWorks: a Task Pane tab with a browser in it, showing /panel (panel.html). The tab is made from outside, over COM
(CreateTaskpaneView2 + the Windows web browser control), so no add-in and no admin registration is needed: SolidWorks only loads add-ins
listed machine-wide (HKLM), which needs admin rights. The tab lives as long as SolidWorks: the loop makes it again after a SolidWorks restart.
The browser control is the old Internet Explorer engine: panel.html is plain ES5 for that reason."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

TITLE = "GWC SolidWorks agent"


def panel_jobs(root: Path) -> list[dict]:
    """Built parts the chat can edit (ready to compare, or being edited now), newest first."""
    out = []
    for state in (Path(root) / "jobs").glob("*/state.json"):
        try:
            r = json.loads(state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if r.get("kind") == "part" and r.get("receipt") and r.get("phase") in ("awaiting_review", "resuming"):
            name = (r.get("draft") or {}).get("part_name") or r["id"]
            out.append({"id": r["id"], "name": name, "phase": r["phase"], "updated": state.stat().st_mtime})
    return sorted(out, key=lambda j: -j["updated"])


def keep_panel(port: int, every: float = 30.0) -> None:
    """Background loop: when SolidWorks runs and has no tab of ours, make it. Never raises."""
    def loop():
        try:
            import pythoncom
            import win32com.client as com
        except ImportError:
            return
        pythoncom.CoInitialize()
        view = None
        while True:
            try:
                if view is None:
                    sw = com.GetActiveObject("SldWorks.Application")
                    view = sw.CreateTaskpaneView2("", TITLE)
                    view.AddControl("Shell.Explorer.2", "").Navigate(f"http://127.0.0.1:{port}/panel")
                else:
                    view.ShowView                         # a property read: raises once SolidWorks is gone, the tab is made again later
            except Exception:                             # noqa: BLE001 - SolidWorks not running / closed: try again later
                view = None
            time.sleep(every)
    # ponytail: a server restart while SolidWorks runs leaves the old tab too (it still works: same page); SolidWorks has no call to list tabs
    threading.Thread(target=loop, daemon=True, name="solidworks-panel").start()
