"""Occurrence-aware text scoring. Does not strip quantities or tolerances."""
from collections import Counter
import re


def normalize(text):
    return re.sub(r"\s+", "", text.casefold().replace("⌀","ø").replace("×","x").replace("−","-"))


def score(reference, extraction):
    expected=Counter(normalize(t) for t in reference["measurement_texts"])
    predicted=Counter(normalize(c["text"]) for c in extraction["callouts"] if c["category"]=="measurement")
    matched=sum((expected & predicted).values())
    section_key=lambda value: re.sub(r"[^a-z0-9]","",value.casefold()).removeprefix("section")
    section_names={section_key(v.get("cutting_plane") or v.get("label", "")) for v in extraction["views"] if v["type"]=="section"}
    return {"reference_status":reference["verification"], "scope":reference["scope"],
            "expected":sum(expected.values()),"predicted":sum(predicted.values()),"matched":matched,
            "recall":matched/sum(expected.values()) if expected else None,
            "precision":matched/sum(predicted.values()) if predicted else 0,
            "missed":list((expected-predicted).elements()),"extra":list((predicted-expected).elements()),
            "expected_views":reference.get("view_count"),"predicted_views":len(extraction["views"]),
            "unit_correct":extraction["unit"]==reference["unit"] if "unit" in reference else None,
            "projection_correct":extraction["projection"]==reference["projection"] if "projection" in reference else None,
            "projection_symbol_presence_correct":extraction.get("projection_symbol",{}).get("status")==reference["projection_symbol_status"] if "projection_symbol_status" in reference else None,
            "section_names_present":all(section_key(n) in section_names for n in reference["section_names"]) if "section_names" in reference else None,
            "required_notes_retained":all(normalize(n) in normalize(" ".join(extraction.get("notes",[]))) for n in reference["required_notes_contains"]) if "required_notes_contains" in reference else None,
            "not_scored":["complete physical-feature reconstruction","all engineering symbols","callout-to-feature attachment accuracy","exact view crop coverage","CAD geometry"]}


def rescore(root, reference_path):
    """Write a new evaluation artifact, preserving trial responses and original reports."""
    import hashlib
    import json
    from pathlib import Path
    root,reference_path=Path(root),Path(reference_path)
    raw=reference_path.read_bytes();reference=json.loads(raw)
    reports=[]
    for path in sorted(root.glob("*/report.json")):
        report=json.loads(path.read_text(encoding="utf-8"))
        if report["status"]=="finished":
            source=json.loads((path.parent/"source.json").read_text(encoding="utf-8"))
            if reference.get("source_sha") and reference["source_sha"]!=source["pages"][0]["sha"]:
                raise ValueError("Reference hash differs from actual source")
            readings=json.loads((path.parent/"readings.json").read_text(encoding="utf-8"))
            report["score"]=score(reference,readings[0]["evidence"]["extraction"])
        report["evaluation_reference_sha"]=hashlib.sha256(raw).hexdigest()
        reports.append(report)
    (root/"rescored-results.json").write_text(json.dumps(reports,indent=2,ensure_ascii=False),encoding="utf-8")
    return reports
