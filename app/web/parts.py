"""Parts on a drawing sheet, a build list per part, and whether SolidWorks is available to build it.

Flow (browser): Review complete -> Parts on this sheet -> per part: create a build list (one model call that sees only that part's views and the
reviewed measurements) or load a prepared build-list file -> the build list is validated offline -> the reviewer ticks the unsupported gaps they
accept -> "Approve and build" starts the existing approval workflow (Build & Verify, then Export). Nothing here builds anything by itself.

Parts come from the sheet's parts list (BOM) when the extraction read one; otherwise from numbered view labels ("01. PISTON"); otherwise the whole
drawing is one part. Views are given to the part whose name they mention, the LONGEST matching name winning ("ROTOR HOUSING" before "ROTOR").
"""
from __future__ import annotations

import io
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

ID = re.compile(r"^[0-9a-f]{12}$")
_ASSEMBLY_VIEW = re.compile(r"\b(assembly|assembled|exploded|parts list|projection symbol)\b", re.I)
_BUY = re.compile(r"\b(bolt|screw|nut|washer|dowel|oil seal|bearing|o-ring|circlip|key)\b", re.I)
_VIEW_WORDS = re.compile(r"\b(front|side|top|end|rear|plan|section|detail|view|isometric|partial|scale)\b.*$", re.I)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(text).lower()).split("(")[0].strip()


def _core(name: str) -> str:
    """'Rotor Housing (Trochoid)' -> 'rotor housing'; 'Hex. Bolt (M10)' -> 'hex bolt'."""
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(name).split("(")[0].lower()).split())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "part"


def detect_parts(result: dict) -> list[dict]:
    """[{id, name, item, quantity, kind: make|buy, views: [{page, no, label, box}]}]"""
    pages = result.get("pages") or []
    views = [{"page": p["no"], "no": v.get("no", i + 1), "label": v.get("label") or "", "box": (p.get("boxes") or [])[v.get("no", i + 1) - 1]
              if v.get("no", i + 1) - 1 < len(p.get("boxes") or []) else None}
             for p in pages for i, v in enumerate((p.get("read") or {}).get("views") or [])]
    bom = [row for p in pages for row in ((((p.get("read") or {}).get("evidence") or {}).get("extraction") or {}).get("bom") or [])]
    parts = []
    if bom:
        for row in bom:
            name = str(row.get("description") or row.get("part_number") or "").strip()
            if name:
                parts.append({"name": name, "item": str(row.get("item") or ""), "quantity": row.get("quantity"),
                              "kind": "buy" if _BUY.search(name) else "make"})
    else:
        seen = set()
        for v in views:
            m = re.match(r"^\s*\d{1,2}\s*[.)]\s*(.+)$", v["label"])
            if not m or _ASSEMBLY_VIEW.search(v["label"]):
                continue
            name = _VIEW_WORDS.sub("", m.group(1).split("(")[0]).strip(" ,/-") or m.group(1).strip()
            if _core(name) and _core(name) not in seen:
                seen.add(_core(name))
                parts.append({"name": name.title(), "item": "", "quantity": None, "kind": "buy" if _BUY.search(name) else "make"})
    if not parts:
        parts = [{"name": "This part", "item": "", "quantity": 1, "kind": "make"}]
    cores = sorted(((_core(p["name"]), p) for p in parts), key=lambda c: -len(c[0]))
    for p in parts:
        p["views"] = []
    for v in views:
        if _ASSEMBLY_VIEW.search(v["label"]) and len(parts) > 1:
            continue
        label = " ".join(re.sub(r"[^a-z0-9 ]+", " ", v["label"].lower()).split())
        hit = next((p for core, p in cores if core and re.search(rf"\b{re.escape(core)}\b", label)), None)
        if hit is None and len(parts) == 1:
            hit = parts[0]
        if hit is not None and v["box"]:
            hit["views"].append(v)
    used = set()
    for p in parts:
        base = _slug(p["name"])
        pid, n = base, 2
        while pid in used:
            pid, n = f"{base}-{n}", n + 1
        used.add(pid)
        p["id"] = pid
    return parts


# ------------------------------------------------------------------ SolidWorks on this machine
def solidworks_status() -> dict:
    """Is SolidWorks installed, running and attachable here? Read-only: never starts SolidWorks."""
    out = {"installed": False, "version": None, "running": False, "attachable": False, "ready": False, "detail": ""}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\SolidWorks") as key:
            names = [winreg.EnumKey(key, i) for i in range(winreg.QueryInfoKey(key)[0])]
        versions = sorted(n for n in names if re.fullmatch(r"SOLIDWORKS \d{4}", n, re.I))
        out.update(installed=bool(versions), version=versions[-1] if versions else None)
    except OSError:
        out["detail"] = "SolidWorks is not installed on this machine (no registry entry)."
        return out
    except ImportError:
        out["detail"] = "SolidWorks detection needs Windows."
        return out
    try:
        import subprocess
        tasks = subprocess.run(["tasklist", "/FI", "IMAGENAME eq SLDWORKS.exe", "/NH"], capture_output=True, text=True, timeout=10).stdout
        out["running"] = "SLDWORKS.exe" in tasks
    except (OSError, subprocess.SubprocessError):
        pass
    if out["running"]:
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            try:
                app = win32com.client.GetActiveObject("SldWorks.Application")
                out.update(attachable=True, revision=str(app.RevisionNumber()))
            finally:
                pythoncom.CoUninitialize()
        except Exception as ex:                                             # noqa: BLE001 - any COM failure means "not attachable"
            out["detail"] = f"SolidWorks is running but cannot be connected to ({type(ex).__name__})."
    out["ready"] = out["installed"] and out["running"] and out["attachable"]
    if not out["detail"]:
        out["detail"] = ("Ready to build." if out["ready"] else
                         "SolidWorks is installed but not running. Start it (with a valid licence) and check again." if out["installed"] else
                         "SolidWorks was not found.")
    return out


# ------------------------------------------------------------------ build lists per part
def part_sheet(result: dict, part: dict, page_png) -> bytes:
    """The part's views stacked into one picture (the model takes one picture per call)."""
    from PIL import Image, ImageDraw
    crops = []
    for v in part["views"][:8]:
        png = page_png(v["page"])
        if png is None:
            continue
        with Image.open(io.BytesIO(png)) as im:
            x0, y0, x1, y1 = (int(round(c)) for c in v["box"])
            crop = im.convert("RGB").crop((max(0, x0 - 8), max(0, y0 - 8), x1 + 8, y1 + 8))
        crop.thumbnail((1400, 900))
        crops.append((v["label"], crop))
    if not crops:
        raise ValueError(f"No views of {part['name']} were located on the drawing")
    w = max(c.width for _, c in crops) + 20
    h = sum(c.height + 34 for _, c in crops) + 10
    sheet = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(sheet)
    y = 6
    for label, crop in crops:
        draw.text((10, y), label[:120], fill="black")
        sheet.paste(crop, (10, y + 22))
        y += crop.height + 34
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    return buf.getvalue()


def _check(draft: dict) -> dict:
    """Validate a build list offline. Unsupported-gap acceptance is the reviewer's decision at build time, so it is reported, not counted as blocking."""
    from app.menu import parse_reply
    from app.runner import validate_list
    from pydantic import ValidationError
    try:
        parsed = parse_reply(draft)
    except ValidationError as ex:
        first = ex.errors()[0]
        return {"status": "invalid", "blocking": [f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}"], "unsupported": []}
    if getattr(parsed, "status", None) == "stopped":
        return {"status": "stopped", "blocking": [getattr(parsed, "reason", "") or "The reader stopped: the part cannot be listed"], "unsupported": []}
    probe = {**draft, "accepted_gaps": [u["id"] for u in draft.get("unsupported", [])]}
    blocking = validate_list(parse_reply(probe))
    unsupported = draft.get("unsupported", [])
    return {"status": "ready" if not blocking else "needs_information", "blocking": blocking, "unsupported": unsupported,
            "features": [it.get("feature") for it in draft.get("items", [])]}


def save_part_list(root: Path, rid: str, part: dict, draft: dict, source: str, llm: str | None = None) -> dict:
    if not ID.fullmatch(rid):
        raise ValueError("invalid drawing result id")
    if draft.get("status") != "stopped":
        draft = {**draft, "accepted_gaps": []}                              # only the reviewer may accept a gap, at build time
    attempt = uuid.uuid4().hex[:12]
    artifact = {"id": attempt, "result_id": rid, "kind": "part_build_list", "part": {k: part[k] for k in ("id", "name", "item", "quantity", "kind")},
                "source": source, "llm": llm, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "draft": draft, "check": _check(draft)}
    folder = root / rid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{attempt}.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=1), encoding="utf-8")
    index = folder / "latest.json"
    latest = json.loads(index.read_text(encoding="utf-8")) if index.is_file() else {}
    latest[part["id"]] = attempt
    index.write_text(json.dumps(latest, indent=1), encoding="utf-8")
    return artifact


def load_part_list(root: Path, rid: str, attempt: str) -> dict | None:
    if not (ID.fullmatch(rid) and ID.fullmatch(attempt)):
        return None
    path = root / rid / f"{attempt}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def latest_lists(root: Path, rid: str) -> dict:
    index = root / rid / "latest.json"
    if not (ID.fullmatch(rid) and index.is_file()):
        return {}
    out = {}
    for pid, attempt in json.loads(index.read_text(encoding="utf-8")).items():
        art = load_part_list(root, rid, attempt)
        if art:
            out[pid] = art
    return out


def reader_context(result: dict, part: dict, model: dict) -> str:
    """Text beside the picture: which part, the drawing's unit/projection, and the measurements the human approved for these views."""
    views = {f"P{v['page']}.v{v['no']}" for v in part["views"]}
    dims = [d.get("corrected_text") or d["text"] for d in model.get("dimensions", []) if set(d.get("views") or []) & views]
    return (f"Write the build list for ONE part only: {part['name']}"
            f"{' (item ' + part['item'] + ')' if part.get('item') else ''}. The picture shows only the views of this part, each with its caption.\n"
            f"Drawing unit: {model.get('unit')}; projection: {model.get('projection')}.\n"
            f"Measurements the human reviewed for these views (use only printed values, as always): {json.dumps(dims, ensure_ascii=False)}")
