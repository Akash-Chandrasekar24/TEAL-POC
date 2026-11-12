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


def _get(obj, name, *args):
    """A COM member read late-bound: pywin32 hands some SolidWorks methods back as properties (a value, not callable)."""
    v = getattr(obj, name)
    if hasattr(v, "_oleobj_"):                  # a COM object already returned (e.g. GetSurface): calling it again fails "Member not found"
        return v
    return v(*args) if callable(v) else v


def _mm(values) -> list[float]:
    return [round(v * 1000, 3) for v in values]


def read_selection() -> list[dict]:
    """What the designer has selected in SolidWorks now (model coordinates, mm): faces with their shape (cylinder axis + diameter, or a
    flat face's outward normal), edges (circle or line), vertices, features. [] when nothing is selected or SolidWorks is not reachable."""
    try:
        import pythoncom
        import win32com.client as com
    except ImportError:
        return []
    pythoncom.CoInitialize()
    try:
        doc = com.GetActiveObject("SldWorks.Application").ActiveDoc
        if doc is None:
            return []
        sel = doc.SelectionManager
        out = []
        for i in range(1, int(_get(sel, "GetSelectedObjectCount2", -1)) + 1):
            kind = int(_get(sel, "GetSelectedObjectType3", i, -1))
            obj, pt = _get(sel, "GetSelectedObject6", i, -1), _get(sel, "GetSelectionPoint2", i, -1)
            item = {"kind": {2: "face", 1: "edge", 3: "vertex", 22: "feature"}.get(kind, f"type {kind}")}   # swSelectType_e: 1 edges, 2 faces
            if pt:
                item["at_model_mm"] = _mm(pt[:3])
            try:
                if kind == 2:
                    surface, outward = _get(obj, "GetSurface"), 1 if _get(obj, "FaceInSurfaceSense") else -1
                    if _get(surface, "IsCylinder"):
                        c = _get(surface, "CylinderParams")
                        item.update(shape="cylinder", axis_point_mm=_mm(c[0:3]), axis=[round(v, 4) for v in c[3:6]], diameter_mm=round(c[6] * 2000, 3))
                    elif _get(surface, "IsPlane"):
                        p = _get(surface, "PlaneParams")
                        item.update(shape="flat", outward_normal=[round(v * outward, 4) for v in p[0:3]])
                    item["made_by"] = _get(_get(obj, "GetFeature"), "Name")
                elif kind == 1:
                    curve = _get(obj, "GetCurve")
                    if _get(curve, "IsCircle"):
                        c = _get(curve, "CircleParams")
                        item.update(shape="circle", centre_mm=_mm(c[0:3]), axis=[round(v, 4) for v in c[3:6]], diameter_mm=round(c[6] * 2000, 3))
                    elif _get(curve, "IsLine"):
                        a, b = _get(_get(obj, "GetStartVertex"), "GetPoint"), _get(_get(obj, "GetEndVertex"), "GetPoint")
                        item.update(shape="line", from_mm=_mm(a), to_mm=_mm(b))
                elif kind == 3:
                    item["point_mm"] = _mm(_get(obj, "GetPoint"))
                elif kind == 22:
                    item["name"] = _get(obj, "Name")
            except Exception as ex:                                     # noqa: BLE001 - a partial description is still useful
                item["note"] = f"not fully read: {type(ex).__name__}"
            out.append(item)
        return out
    except Exception:                                                   # noqa: BLE001 - SolidWorks not running / busy
        return []
    finally:
        pythoncom.CoUninitialize()


def say_selection(sel: list[dict]) -> str:
    """One line per selected item, for the chat and for the agent."""
    lines = []
    for s in sel:
        what = {"cylinder": f"round face Ø{s.get('diameter_mm')} mm, axis {s.get('axis')} through {s.get('axis_point_mm')} mm",
                "flat": f"flat face, outward normal {s.get('outward_normal')}",
                "circle": f"round edge Ø{s.get('diameter_mm')} mm, centre {s.get('centre_mm')} mm, axis {s.get('axis')}",
                "line": f"straight edge from {s.get('from_mm')} to {s.get('to_mm')} mm"}.get(s.get("shape"), s["kind"])
        where = f", clicked at {s['at_model_mm']} mm" if s.get("at_model_mm") else ""
        by = f", made by {s['made_by']}" if s.get("made_by") else f" {s['name']}" if s.get("name") else ""
        lines.append(what + by + where)
    return "; ".join(lines)


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
