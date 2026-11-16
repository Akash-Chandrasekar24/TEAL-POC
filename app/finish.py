"""FINISH (step E): save the SLDPRT, export STEP and STL, render a preview PNG, and check what was written. Plain code, no LLM.

Checks (each one is reported; any failure makes the report not ok):
  - every file exists and is not empty;
  - the STEP's length unit is the list's unit (a part built from the mm template exports STEP in mm; the default template exports inches);
  - the STL's SIZE (STL is always mm) matches the size of the model's own exact bounding box, when one is given. Positions are not compared:
    SolidWorks writes the STL shifted into positive space (measured 2026-10-13: model box -60..60 x -40..40 x 0..18, STL box 0..120 x 0..80 x 0..18);
  - the preview PNG was written.
`name` becomes the file name: keep it simple (letters, digits, - and _).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.mcp_client import SolidWorksClient
from app.preview import read_stl, render_stl_png


@dataclass
class FinishReport:
    ok: bool = False
    files: dict[str, str] = field(default_factory=dict)
    checks: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stl_bbox_mm: list[list[float]] | None = None

    def lines(self) -> list[str]:
        out = [f"{'OK' if self.ok else 'FAILED'}: finish"]
        out += [f"  {k}: {v}" for k, v in self.files.items()]
        out += [f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}" for c in self.checks]
        out += [f"  warning: {w}" for w in self.warnings]
        return out


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "part"


def step_unit(path: Path) -> str:
    """'mm' | 'inch' | 'unknown' from the STEP header / units entities."""
    text = path.read_text(errors="replace")
    if "MILLI" in text:
        return "mm"
    if "INCH" in text.upper():
        return "inch"
    return "unknown"


async def finish_part(sw: SolidWorksClient, out_dir: Path, name: str, unit: str, *, expected_bbox_mm: list[list[float]] | None = None,
                      bbox_tol_mm: float = 0.5, review_only: bool = False) -> FinishReport:
    """Save + export the ACTIVE part into out_dir (created if needed; files of the same name are replaced)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_name(name)
    paths = {"SLDPRT": out_dir / f"{stem}.SLDPRT", "STEP": out_dir / f"{stem}.step", "STL": out_dir / f"{stem}.stl", "PREVIEW": out_dir / f"{stem}_preview.png"}
    if review_only:
        paths.pop("STEP")
    report = FinishReport(files={k: str(v) for k, v in paths.items()})
    for key in paths:
        paths[key].unlink(missing_ok=True)

    def check(label: str, ok: bool, detail: str) -> bool:
        report.checks.append({"name": label, "ok": ok, "detail": detail})
        return ok

    if "ext_clear_selection" in (getattr(sw, "tool_names", None) or []):       # an STL is written for the SELECTED body only otherwise
        await sw.call("ext_clear_selection")
    saved = await sw.save_document(paths["SLDPRT"])
    check("save SLDPRT", saved.ok and paths["SLDPRT"].is_file(), saved.text[:120])
    if not review_only:
        step = await sw.export_document("STEP", paths["STEP"])
        check("export STEP", step.ok and paths["STEP"].is_file(), step.text[:120])
    stl = await sw.export_document("STL", paths["STL"])
    check("export STL", stl.ok and paths["STL"].is_file(), stl.text[:120])
    for key in (key for key in paths if key != "PREVIEW"):
        if paths[key].is_file():
            check(f"{key} not empty", paths[key].stat().st_size > 0, f"{paths[key].stat().st_size:,} bytes")

    if "STEP" in paths and paths["STEP"].is_file() and paths["STEP"].stat().st_size > 0:
        got = step_unit(paths["STEP"])
        check("STEP length unit", got == unit, f"STEP is {got}, the list's unit is {unit}")
    if paths["STL"].is_file() and paths["STL"].stat().st_size > 0:
        try:
            tri = read_stl(paths["STL"]).reshape(-1, 3)
            lo, hi = tri.min(axis=0), tri.max(axis=0)
            report.stl_bbox_mm = [[round(float(v), 3) for v in lo], [round(float(v), 3) for v in hi]]
            if expected_bbox_mm:
                stl_size = [h - l for l, h in zip(*report.stl_bbox_mm)]
                model_size = [h - l for l, h in zip(*expected_bbox_mm)]
                diffs = [abs(a - b) for a, b in zip(stl_size, model_size)]
                # a mesh of large curved faces lies a little inside them (an R80 crank web: 0.58 mm on 204): 0.5 % of the size, at least the tolerance
                tol = max(bbox_tol_mm, 0.005 * max(model_size))
                check("STL size = the model's size", max(diffs) <= tol,
                      f"STL {[round(v, 3) for v in stl_size]} mm, model {[round(v, 3) for v in model_size]} mm, largest difference {max(diffs):.3f} mm")
            png = render_stl_png(paths["STL"], paths["PREVIEW"], stem)
            check("preview PNG", png.is_file() and png.stat().st_size > 0, f"{png.stat().st_size:,} bytes")
        except Exception as ex:                                              # a bad STL must not crash the finish: it is reported
            check("read the STL / render the preview", False, f"{type(ex).__name__}: {ex}")
    report.ok = bool(report.checks) and all(c["ok"] for c in report.checks)
    return report
