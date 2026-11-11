"""Drawing vs model, per build step: what the step added in SolidWorks (its new faces: every hole's real diameter and centre, the base's real
size) and how far each number is from the drawing's. The SolidWorks page draws a marker per step on the 3D model; a click shows the table."""
from __future__ import annotations

from typing import Any

from app.features.base import factor


def signature(face: dict) -> tuple:
    """A face, recognisable after later steps (rounded to 1 micron / 1e-9 m2)."""
    r = lambda v: tuple(round(x, 6) for x in v) if isinstance(v, (list, tuple)) else v
    return (face.get("kind") or ("plane" if face.get("planar") else "?"), r(face.get("origin")), r(face.get("axis")), r(face.get("normal")),
            round(face.get("radius") or 0.0, 6), r(face.get("point")), round(face.get("area") or 0.0, 9))


def _numbers(params: Any, path: str = ""):
    """(key path, value) of every number in an item's params."""
    if isinstance(params, dict):
        for k, v in params.items():
            yield from _numbers(v, f"{path}.{k}" if path else k)
    elif isinstance(params, list):
        for i, v in enumerate(params):
            yield from _numbers(v, f"{path}[{i}]")
    elif isinstance(params, (int, float)) and not isinstance(params, bool):
        yield path, float(params)


def _check(what: str, drawing: float, model: float | None, unit: str = "mm") -> dict:
    dev = None if model is None or not drawing else (model - drawing) / abs(drawing) * 100
    return {"what": what, "drawing": round(drawing, 3), "model": None if model is None else round(model, 3), "unit": unit,
            "deviation_pct": None if dev is None else round(dev, 3)}


def step_measurement(item, unit: str, new_faces: list[dict], verdicts: list[dict], bbox_mm: list[list[float]] | None = None,
                     base_size: tuple[float, float, float] | None = None) -> dict:
    """{anchor_mm, checks, faces}: where the step is in the model and its numbers against the drawing's."""
    mm = factor(unit) * 1000
    cyl = [f for f in new_faces if f.get("kind") == "cylinder" and f.get("radius")]
    pts = [f["origin"] for f in cyl] or [f["point"] for f in new_faces if f.get("point")]
    anchor = [round(sum(p[a] for p in pts) / len(pts) * 1000, 3) for a in range(3)] if pts else None
    if bbox_mm and anchor is None:
        anchor = [round((bbox_mm[0][a] + bbox_mm[1][a]) / 2, 3) for a in range(3)]
    checks, notes = [], []
    diam = sorted({round(2 * f["radius"] * 1000, 4) for f in cyl})
    face_of = {round(2 * f["radius"] * 1000, 4): f for f in cyl}
    selector = getattr(item, "feature", "") == "fillet_chamfer_edges"          # its radius picks the EDGE to round: not a drawing size
    for key, v in _numbers(getattr(item, "params", {}) or {}):
        leaf = key.rsplit(".", 1)[-1].split("[")[0]
        if not diam or v <= 0 or (selector and leaf == "radius"):
            continue
        if "diameter" in leaf:
            want = v * mm
            got = min(diam, key=lambda d: abs(d - want))
            checks.append(_check(f"Ø {key}", want, got))
        elif leaf == "radius":
            want = 2 * v * mm
            got = min(diam, key=lambda d: abs(d - want))
            checks.append(_check(f"R {key}", v * mm, got / 2))
        else:
            continue
        notes.append({"point": face_of[got]["point"], "checks": [len(checks) - 1]})     # a note on that face in SolidWorks (text: note_texts)
    if base_size and bbox_mm:                                                  # the base: its outline and thickness against the part's real size
        ext = [bbox_mm[1][a] - bbox_mm[0][a] for a in range(3)]
        for name, want, got in zip(("width (x)", "height (y)", "thickness (z)"), base_size, ext):
            checks.append(_check(f"base {name}", want * mm, got))
        top = max((f for f in new_faces if f.get("planar") and f.get("point")), key=lambda f: f["point"][2], default=None)
        if top is not None:
            notes.append({"point": top["point"], "checks": [len(checks) - 3, len(checks) - 2, len(checks) - 1]})
    for v in verdicts:
        exp, got = v.get("expected_delta_mm3"), v.get("measured_delta_mm3")
        if exp and got is not None:
            checks.append(_check(f"volume {'added' if exp > 0 else 'removed'} ({v.get('name', '')})", abs(exp), abs(got), "mm³"))
    return {"anchor_mm": anchor, "checks": checks, "diameters_mm": diam[:12], "notes": notes[:8]}


def judged(checks: list[dict], printed: str, general: str | None, unit: str) -> list[dict]:
    """Every check with the drawing's tolerance band and whether the model is inside it (app.drawing.tolerance)."""
    from app.drawing.tolerance import judge
    return [judge(c, printed, general, factor(unit) * 1000) for c in checks]


def note_texts(item_id: str, measured: dict, printed: str, general: str | None, unit: str) -> list[dict]:
    """The SolidWorks notes of one step: "F2 Ø50.00 | drawing Ø50 ±0.30 (ISO 2768-m) | within" on the face it measured."""
    checks = judged(measured.get("checks", []), printed, general, unit)
    out = []
    for note in measured.get("notes", []):
        parts = []
        for i in note.get("checks", []):
            if i >= len(checks):
                continue
            c = checks[i]
            sym = "Ø" if c["what"].startswith("Ø") else "R" if c["what"].startswith("R") else ""
            tol = c.get("tol")
            band = "" if not tol else f" ±{tol[1]:g}" if tol[0] == -tol[1] else f" +{tol[1]:g}/{tol[0]:g}"
            verdict = "within" if c.get("within") else "OUT by %.3f" % (abs(c["model"] - c["drawing"]) - (tol[1] if c["model"] > c["drawing"] else -tol[0])) if tol else "no tolerance"
            parts.append(f"{sym}{c['model']:.2f} (drawing {sym}{c['drawing']:g}{band}) {verdict}")
        if parts:
            src = checks[note["checks"][0]].get("tol_source", "")
            out.append({"point": note["point"], "text": f"{item_id} " + " | ".join(parts) + (f" [{src}]" if src else "")})
    return out
