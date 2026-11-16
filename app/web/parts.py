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
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

ID = re.compile(r"^[0-9a-f]{12}$")
_LATEST = threading.Lock()                                                   # build lists are written in parallel: one writer of latest.json
_ASSEMBLY_VIEW = re.compile(r"\b(assembly|assembled|exploded|parts list|projection symbol)\b", re.I)
_MATERIAL_SPLIT = re.compile(r"\s+[—–-]\s+")      # "Pump Casing — Cast Iron (FC250)": the parts list's material column read into the name
_VIEW_WORDS = re.compile(r"\b(front|side|top|end|rear|plan|section|detail|view|isometric|partial|scale)\b.*$", re.I)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(text).lower()).split("(")[0].strip()


def _core(name: str) -> str:
    """'Rotor Housing (Trochoid)' -> 'rotor housing'; 'Hex. Bolt (M10)' -> 'hex bolt'."""
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(name).split("(")[0].lower()).split())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "part"


def detect_parts(result: dict) -> list[dict]:
    """[{id, name, item, quantity, views: [{page, no, label, box}]}]: every part is built in 3D, bearings, seals and bolts included."""
    pages = result.get("pages") or []
    views = [{"page": p["no"], "no": v.get("no", i + 1), "label": v.get("label") or "", "box": (p.get("boxes") or [])[v.get("no", i + 1) - 1]
              if v.get("no", i + 1) - 1 < len(p.get("boxes") or []) else None}
             for p in pages for i, v in enumerate((p.get("read") or {}).get("views") or [])]
    bom = [row for p in pages for row in ((((p.get("read") or {}).get("evidence") or {}).get("extraction") or {}).get("bom") or [])]
    parts = []
    if bom:
        for row in bom:
            name, *material = _MATERIAL_SPLIT.split(str(row.get("description") or row.get("part_number") or "").strip(), maxsplit=1)
            if name:
                parts.append({"name": name, "item": str(row.get("item") or ""), "quantity": row.get("quantity"),
                              **({"material": material[0]} if material else {})})
    else:
        seen = set()
        for v in views:
            m = re.match(r"^\s*\d{1,2}\s*[.)]\s*(.+)$", v["label"])
            if not m or _ASSEMBLY_VIEW.search(v["label"]):
                continue
            name = _VIEW_WORDS.sub("", m.group(1).split("(")[0]).strip(" ,/-") or m.group(1).strip()
            if _core(name) and _core(name) not in seen:
                seen.add(_core(name))
                parts.append({"name": name.title(), "item": "", "quantity": None})
    if not parts:
        parts = [{"name": "This part", "item": "", "quantity": 1}]
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
    _apply_view_map(result, parts, views)
    used = set()
    for p in parts:
        base = _slug(p["name"])
        pid, n = base, 2
        while pid in used:
            pid, n = f"{base}-{n}", n + 1
        used.add(pid)
        p["id"] = pid
    return parts


# ------------------------------------------------------------------ which part each view shows (one AI call per drawing)
# Name matching misses every view whose label does not name its part: "SECTION A-A" of the piston, the unlabelled main view of a connecting
# rod (seen on a 4-cylinder engine sheet: the rod got only a small bolting view and its list was written from the cap's sizes). The AI sees
# the sheet with each view boxed and numbered and says which part every view shows, and how many of each part the assembly uses.
VIEWS_PROMPT = """The picture is an engineering drawing sheet. Every view on it is boxed in red and numbered "page.view" (e.g. 1.4).
The parts on the sheet are listed below. For EVERY numbered view say which ONE part it shows, by its exact name from the list:
section views belong to the part whose view carries their cutting line (SECTION A-A belongs to the part with the A-A arrows), an
unlabelled view belongs to the part it draws (match shapes and sizes with the labelled views). Use "assembly" for an isometric, exploded or
assembled view that shows several parts, and "other" for a symbol, title block or table.
Also give how many of each part ONE assembly uses, from the assembly / isometric view, the parts list and the title (a 4-cylinder engine has
4 pistons), with the evidence.
Return JSON only: {"views": [{"view": "1.4", "part": "<exact part name>|assembly|other", "why": "..."}],
"quantities": [{"part": "<exact part name>", "quantity": 4, "evidence": "..."}]}"""


def view_map_path(rid: str) -> Path:
    from app import config
    return config.PROJECT_ROOT / "storage/parts" / rid / "views.json"


def numbered_sheet(result: dict, page_png) -> bytes:
    """Every page with each view's box drawn in red and numbered page.view (one picture: the model takes one per call)."""
    from PIL import Image, ImageDraw
    pages = []
    for p in result.get("pages") or []:
        png = page_png(p["no"])
        if png is None:
            continue
        im = Image.open(io.BytesIO(png)).convert("RGB")
        k = max(1.0, 1600 / max(im.size))
        im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
        draw = ImageDraw.Draw(im)
        for i, v in enumerate((p.get("read") or {}).get("views") or []):
            no, boxes = v.get("no", i + 1), p.get("boxes") or []
            if no - 1 < len(boxes) and boxes[no - 1]:
                x0, y0, x1, y1 = (c * k for c in boxes[no - 1])
                draw.rectangle((x0, y0, x1, y1), outline=(220, 0, 0), width=3)
                draw.rectangle((x0, y0, x0 + 46, y0 + 22), fill=(220, 0, 0))
                draw.text((x0 + 4, y0 + 4), f"{p['no']}.{no}", fill="white")
        pages.append(im)
    if not pages:
        raise ValueError("The drawing's pages are not available")
    w, h = max(i.width for i in pages), sum(i.height for i in pages) + 10 * (len(pages) - 1)
    sheet = Image.new("RGB", (w, h), "white")
    y = 0
    for im in pages:
        sheet.paste(im, (0, y)); y += im.height + 10
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    return buf.getvalue()


def _apply_view_map(result: dict, parts: list[dict], views: list[dict]) -> None:
    """Add the views name matching left out to the part the AI said they show; fill a quantity the sheet does not print."""
    path = view_map_path(str(result.get("id", ""))) if ID.fullmatch(str(result.get("id", ""))) else None
    if path is None or not path.is_file() or len(parts) < 2:
        return
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    by_core = {_core(p["name"]): p for p in parts}
    taken = {(v["page"], v["no"]) for p in parts for v in p["views"]}
    for v in views:
        part = by_core.get(_core((saved.get("views") or {}).get(f"{v['page']}.{v['no']}", "")))
        if part is not None and v["box"] and (v["page"], v["no"]) not in taken:
            part["views"].append(v)
    for p in parts:
        q = (saved.get("quantities") or {}).get(_core(p["name"]))
        if p.get("quantity") in (None, "") and isinstance(q, int) and q > 0:
            p["quantity"], p["quantity_from"] = q, "AI: assembly view / title"


def parse_view_map(reply: dict, parts: list[dict]) -> dict:
    """The model's answer -> {"views": {"1.4": "<part name>"}, "quantities": {"<part core>": n}}; unknown names are dropped."""
    names = {_core(p["name"]): p["name"] for p in parts}
    names.setdefault("assembly", "assembly")                                    # the isometric / exploded / assembled views
    views = {str(v.get("view")): names[_core(v.get("part", ""))] for v in reply.get("views") or []
             if isinstance(v, dict) and _core(v.get("part", "")) in names}
    quantities = {}
    for q in reply.get("quantities") or []:
        if isinstance(q, dict) and _core(q.get("part", "")) in names and isinstance(q.get("quantity"), int) and q["quantity"] > 0:
            quantities[_core(q["part"])] = q["quantity"]
    return {"views": views, "quantities": quantities, "evidence": reply}


# ------------------------------------------------------------------ SolidWorks on this machine
_STATUS: list = [0.0, None]          # (time, result): the page polls every 2.5 s; the check (process list + COM) takes ~0.2 s


def solidworks_status() -> dict:
    """solidworks_status_now(), reused for 10 s (a status that goes stale for a few seconds only delays the button)."""
    import time
    if _STATUS[1] is None or time.monotonic() - _STATUS[0] > 10:
        _STATUS[:] = [time.monotonic(), solidworks_status_now()]
    return dict(_STATUS[1])


def solidworks_status_now() -> dict:
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
                rev = app.RevisionNumber                                     # late-bound COM: a value, or a method on some builds
                out.update(attachable=True, revision=str(rev() if callable(rev) else rev))
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
MIN_SIDE = 2000          # a page smaller than this is enlarged before the AI reads it (a 440 px scan has unreadable dimension text)


def whole_pages(result: dict, page_png) -> bytes:
    """Every page of the drawing, stacked and enlarged to a readable size: what the AI sees when the drawing is ONE part (cropping its
    views only loses information: measured on a 440 px sheet whose view boxes came back as four fragments of one view)."""
    from PIL import Image
    pages = []
    for p in result.get("pages") or []:
        png = page_png(p["no"])
        if png is None:
            continue
        im = Image.open(io.BytesIO(png)).convert("RGB")
        if max(im.size) < MIN_SIDE:
            k = MIN_SIDE / max(im.size)
            im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
        pages.append(im)
    if not pages:
        raise ValueError("The drawing's pages are not available")
    w, h = max(i.width for i in pages), sum(i.height for i in pages) + 10 * (len(pages) - 1)
    sheet = Image.new("RGB", (w, h), "white")
    y = 0
    for im in pages:
        sheet.paste(im, (0, y)); y += im.height + 10
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    return buf.getvalue()


def part_sheet(result: dict, part: dict, page_png) -> bytes:
    """The part's views stacked into one picture (the model takes one picture per call)."""
    from PIL import Image, ImageDraw
    crops = []
    mine = {(v["page"], v["no"]) for v in part["views"]}
    for v in part["views"][:8]:
        png = page_png(v["page"])
        if png is None:
            continue
        with Image.open(io.BytesIO(png)) as im:
            page = im.convert("RGB")
            # blank the OTHER views that reach into this crop: a crankshaft's box took in the piston pin's "Ø33.8" and "82", and the
            # SolidWorks agent added a Ø33.8 x 82 shaft to the crankshaft
            pg = next((p for p in result.get("pages") or [] if p["no"] == v["page"]), {})
            boxes = pg.get("boxes") or []
            for i, other in enumerate((pg.get("read") or {}).get("views") or []):
                no = other.get("no", i + 1)
                if (v["page"], no) not in mine and no - 1 < len(boxes) and boxes[no - 1]:
                    ImageDraw.Draw(page).rectangle(tuple(int(round(c)) for c in boxes[no - 1]), fill="white")
            x0, y0, x1, y1 = (int(round(c)) for c in v["box"])
            crop = page.crop((max(0, x0 - 8), max(0, y0 - 8), x1 + 8, y1 + 8))
        if max(crop.size) < 900:                                            # a small view of a low-resolution scan: enlarge it to be readable
            k = 900 / max(crop.size)
            crop = crop.resize((int(crop.width * k), int(crop.height * k)), Image.LANCZOS)
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
    artifact = {"id": attempt, "result_id": rid, "kind": "part_build_list", "part": {k: part[k] for k in ("id", "name", "item", "quantity")},
                "source": source, "llm": llm, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "draft": draft, "check": _check(draft)}
    folder = root / rid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{attempt}.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=1), encoding="utf-8")
    index = folder / "latest.json"
    with _LATEST:
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


WORK_IT_OUT = ("The designer asked YOU to work out what the sheet does not print. Use, in this order: the sizes of the parts it fits on this sheet "
               "(a bore, cavity, shaft or seat it sits in or on), the assembly and section views, and the standard catalogue sizes of a standard part "
               "named on the sheet (e.g. a 6306 bearing, an M10 bolt). Write EVERY number you worked out with derived: true and derived_from starting "
               "'ASSUMED:' (say where it came from) and mark that item uncertain. Put in needs_human ONLY the one or two assumptions that change the shape most "
               "(never one per number). "
               "Do not stop and do not return an empty list: write the best complete build list you can.")


def reader_context(result: dict, part: dict, model: dict, answers: list | None = None, work_it_out: bool = False) -> str:
    """Text beside the picture: which part, the drawing's unit/projection, the measurements the human approved for these views, the designer's
    answers to earlier questions, and (on request) leave to work out unprinted sizes as marked assumptions."""
    views = {f"P{v['page']}.v{v['no']}" for v in part["views"]}
    dims = [d.get("corrected_text") or d["text"] for d in model.get("dimensions", []) if set(d.get("views") or []) & views]
    if not part["views"]:                                                  # not drawn on its own: every reviewed value on the sheet may help
        dims = [d.get("corrected_text") or d["text"] for d in model.get("dimensions", [])][:300]
    text = (f"Write the build list for ONE part only: {part['name']}"
            f"{' (item ' + part['item'] + ')' if part.get('item') else ''}"
            f"{'. The picture shows only the views of this part, each with its caption' if part['views'] else '. The part has no view of its own: the picture shows the assembly and section views where it appears'}.\n"
            "Model it as ONE solid part even when it is a standard or multi-piece component (a bearing, a seal, a bolt): never stop for that.\n"
            "needs_human: at most 3 short questions, only real doubts that change the 3D shape; combine related ones; never ask to confirm a printed value, "
            "a simplification or a modelling choice.\n"
            f"Drawing unit: {model.get('unit')}; projection: {model.get('projection')}.\n"
            f"Measurements the human reviewed for these views (use only printed values, as always): {json.dumps(dims, ensure_ascii=False)}")
    answered = [a for a in (answers or []) if isinstance(a, dict) and str(a.get("answer", "")).strip()]
    if answered:
        text += ("\nThe designer answered these questions (an answer 'AI decides' means: keep your best assumption). Treat each answer as a printed value, "
                 "list the numbers in seen_dimensions, and do not ask about any of them again: "
                 + json.dumps([{"question": str(a.get("question", ""))[:600], "answer": str(a["answer"])[:600]} for a in answered], ensure_ascii=False))
    if work_it_out:
        text += "\n" + WORK_IT_OUT
    return text


def assembly_views(result: dict) -> list[dict]:
    """The assembly / section / exploded views of the sheet: where a part with no view of its own is still drawn. With a view map (one AI
    call, view_map_path) the views it calls "assembly" are used: an UNLABELLED isometric of the whole engine is one, a part's own section is not."""
    saved = {}
    path = view_map_path(str(result.get("id", ""))) if ID.fullmatch(str(result.get("id", ""))) else None
    if path is not None and path.is_file():
        try:
            saved = json.loads(path.read_text(encoding="utf-8")).get("views") or {}
        except (OSError, ValueError):
            saved = {}
    out = []
    for p in result.get("pages") or []:
        for i, v in enumerate((p.get("read") or {}).get("views") or []):
            no, boxes = v.get("no", i + 1), p.get("boxes") or []
            label = v.get("label") or ""
            if "assembly" in saved.values():
                wanted = saved.get(f"{p['no']}.{no}") == "assembly"
            else:
                wanted = bool(_ASSEMBLY_VIEW.search(label) or re.search(r"\bsection\b", label, re.I)
                              or str(v.get("type", "")).lower() in ("iso", "isometric", "exploded", "assembly"))
            if wanted and no - 1 < len(boxes):
                out.append({"page": p["no"], "no": no, "label": label or "assembly view", "box": boxes[no - 1]})
    return out


def confirm_part_list(root: Path, rid: str, part: dict, art: dict, answers: list | None = None) -> dict:
    """The designer confirms the list AS WRITTEN (the AI's assumptions included): the questions are closed with their answers, the uncertain
    readings are confirmed. A human decision; the check still has to pass, and the build still waits for the approval."""
    draft = json.loads(json.dumps(art["draft"]))
    if not draft.get("items"):
        raise ValueError("This build list has no 3D steps to confirm: send answers or let the AI work it out first")
    given = {str(a.get("question", "")): str(a.get("answer", "")).strip() for a in (answers or []) if isinstance(a, dict)}
    draft["designer_answers"] = [{"question": q, "answer": given.get(q) or "Confirmed as written"} for q in draft.get("needs_human", [])]
    draft["needs_human"] = []
    for it in draft.get("items", []):
        it["uncertain"] = False
    return save_part_list(root, rid, part, draft, "designer", art.get("llm"))
