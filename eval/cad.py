"""Offline CAD coverage inventory. No model calls or SolidWorks session.

Run: python -m eval.cad [--out docs/cad-coverage.md] [--json]
Requirements are engineer-authored annotations, not guesses based on part names.
Menu availability is conditional on its limits, not proof a drawing can be built.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.menu import MENU, parse_reply
from app.menu.buildlist import BuildList
from app.runner import validate_list

ROOT = Path(__file__).resolve().parents[1]

# Deliberately separate from MENU: planned operations must never reach a reader
# prompt or become executable just because they appear in the roadmap.
PLANNED = {
    "datum_planes": "General rotated/offset planes and axes",
    "two_view_base": "Base formed from a plan outline and varying side profile",
    "turned_followups": "End-face operations, cross holes, flats and keyways after revolve_base",
    "termination": "Up-to-face/next and selected-body termination",
    "general_ribs": "Ribs in arbitrary orientations",
    "general_edges": "Semantic edge selection beyond front/back base planes",
    "revolve_cut": "True material-removing revolve",
    "sweep": "Boss/cut along an explicit path",
    "shell": "Thin-wall construction",
    "draft": "Tapered faces",
    "loft": "Transitions between sections",
    "multibody": "Explicit body targeting and booleans",
    "sheet_metal": "Bends and flat-pattern-aware parts",
    "assembly": "Components and mates",
}


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    reviewed: bool = False
    required_capabilities: list[str] = Field(default_factory=list)
    evidence: str = ""


def inventory(benchmark: dict, annotations: dict, root: Path = ROOT) -> dict:
    if annotations.get("version") != 1:
        raise ValueError("CAD requirements version must be 1")
    sets = benchmark["sets"]
    known_ids = {s["id"] for s in sets}
    if len(known_ids) != len(sets):
        raise ValueError("duplicate benchmark ids")
    assessments = {}
    for raw in annotations.get("cases", []):
        a = Assessment.model_validate(raw)
        if a.id not in known_ids or a.id in assessments:
            raise ValueError(f"unknown or duplicate assessment id: {a.id}")
        unknown = set(a.required_capabilities) - set(MENU) - set(PLANNED)
        if unknown:
            raise ValueError(f"{a.id}: unknown capabilities: {sorted(unknown)}")
        if a.reviewed and (not a.required_capabilities or not a.evidence.strip()):
            raise ValueError(f"{a.id}: reviewed requirements need capabilities and evidence")
        assessments[a.id] = a

    cases, gaps = [], Counter()
    for s in sets:
        a = assessments.get(s["id"], Assessment(id=s["id"]))
        missing = sorted(set(a.required_capabilities) - set(MENU))
        if a.reviewed and not s.get("synthetic"):
            gaps.update(missing)
        reference = s.get("build_list")
        reasons = []
        reference_status = "missing"
        if reference is not None:
            try:
                data = reference if isinstance(reference, dict) else json.loads((root / reference).read_text(encoding="utf-8"))
                bl = parse_reply(data)
                reasons = validate_list(bl) if isinstance(bl, BuildList) else ["reference is a stopped reader reply"]
            except (ValueError, OSError, TypeError) as ex:
                reasons = [str(ex)]
            reference_status = "invalid" if reasons else "offline_valid"
        verified = bool(s.get("verified"))
        # Even this state promises only offline eligibility, never CAD accuracy.
        eligible = verified and a.reviewed and not missing and reference_status == "offline_valid"
        cases.append({"id": s["id"], "synthetic": bool(s.get("synthetic")), "drawing_verified": verified,
                      "requirements_reviewed": a.reviewed, "required_capabilities": a.required_capabilities,
                      "missing_capabilities": missing, "evidence": a.evidence,
                      "reference_status": reference_status, "reference_reasons": reasons,
                      "offline_eligible": eligible, "live_status": "not_measured"})
    return {
        "summary": {"sets": len(cases), "real_drawings": sum(not c["synthetic"] for c in cases),
                    "verified_drawings": sum(c["drawing_verified"] for c in cases),
                    "reviewed_requirements": sum(c["requirements_reviewed"] for c in cases),
                    "offline_valid_references": sum(c["reference_status"] == "offline_valid" for c in cases),
                    "offline_eligible": sum(c["offline_eligible"] for c in cases)},
        "execution": {"source_present": (root / "third_party/mcp-server-solidworks-main").is_dir(),
                      "default_server_present": (root / "build/execution-bin/SolidworksExecution.exe").is_file()},
        "menu": [{"name": e.name, "limits": list(e.limits)} for e in MENU.values()],
        "planned": PLANNED,
        "reviewed_gap_counts": dict(sorted(gaps.items(), key=lambda kv: (-kv[1], kv[0]))),
        "cases": cases,
    }


def markdown(report: dict) -> str:
    s = report["summary"]
    lines = ["# CAD coverage baseline", "",
             f"{s['sets']} sets; {s['real_drawings']} real drawings; {s['verified_drawings']} verified drawings; "
             f"{s['reviewed_requirements']} reviewed requirement sets; {s['offline_valid_references']} offline-valid reference lists.", "",
             "No live CAD coverage score is available. Menu membership is subject to each feature's limits. "
             "Draft requirements and synthetic variants are excluded from gap ranking. Offline eligibility does not prove drawing fidelity.", "",
             "| Drawing | Truth | Requirements | Reference list | Missing capabilities (draft if unreviewed) |",
             "|---|---|---|---|---|"]
    for c in report["cases"]:
        missing = ", ".join(c["missing_capabilities"]) or ("none identified" if c["requirements_reviewed"] else "unassessed")
        lines.append(f"| {c['id']} | {'verified' if c['drawing_verified'] else 'draft'} | "
                     f"{'reviewed' if c['requirements_reviewed'] else 'pending review'} | {c['reference_status']} | "
                     f"{missing} |")
    lines += ["", "## Gaps from reviewed requirements", ""]
    lines += [f"- {name}: {count} drawing set(s)" for name, count in report["reviewed_gap_counts"].items()] or [
        "No reviewed requirements yet; a ranked feature backlog would be speculative."]
    lines += ["", "## Current menu limits", ""]
    for e in report["menu"]:
        lines += [f"- **{e['name']}**: " + "; ".join(e["limits"])]
    lines += ["", "## Execution prerequisites", "",
              f"- Third-party source present: {report['execution']['source_present']}",
              f"- Default compiled server present: {report['execution']['default_server_present']}",
              "- These are file-presence checks only; they do not test SolidWorks installation, licensing, or custom executable paths."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, default=ROOT / "eval/benchmark.json")
    parser.add_argument("--requirements", type=Path, default=ROOT / "eval/cad_requirements.json")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = inventory(json.loads(args.benchmark.read_text(encoding="utf-8")),
                       json.loads(args.requirements.read_text(encoding="utf-8")))
    output = json.dumps(report, indent=2, ensure_ascii=False) + "\n" if args.json else markdown(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
