"""Scorers of the benchmark: dimensions (as printed) and views (boxes vs truth points). Plain code, no model."""
from __future__ import annotations

from collections import Counter

from app.perception.dims import dim_tokens  # noqa: F401  (re-exported: the app and the benchmark normalise dimensions the same way)


def score_dimensions(truth: list[str], predicted: list[str], complete: bool = True) -> dict:
    t = Counter(tok for s in truth for tok in dim_tokens(s))
    p = Counter(tok for s in predicted for tok in dim_tokens(s))
    matched = sum((t & p).values())
    return {"truth": sum(t.values()), "predicted": sum(p.values()), "matched": matched,
            "recall": matched / sum(t.values()) if t else None,
            "precision": (matched / sum(p.values()) if p else 0.0) if complete else None,
            "missed": sorted((t - p).elements())[:20]}


def score_dimension_set(truth: list[str], predicted: list[str]) -> dict:
    """Recall / precision over DISTINCT dimension tokens: fair when the pictures overlap (tiles, crops) and a callout is read twice."""
    t = {tok for s in truth for tok in dim_tokens(s)}
    p = {tok for s in predicted for tok in dim_tokens(s)}
    return {"truth": len(t), "predicted": len(p), "matched": len(t & p), "recall": len(t & p) / len(t) if t else None,
            "precision": len(t & p) / len(p) if p else 0.0, "missed": sorted(t - p), "extra": sorted(p - t)}


def _inside(point, box, tol: float = 0.0) -> bool:
    x, y = point
    x0, y0, x1, y1 = box
    return x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol


def _tol(box) -> float:
    """Slack for HAND-PLACED extent points (a point drawn on a dimension just outside the part): 3% of the box's larger side + 5 px."""
    return 0.03 * max(box[2] - box[0], box[3] - box[1]) + 5


def score_views(truth: list[dict], boxes: dict[int, list[list[float]]]) -> dict:
    """truth: [{"page", "point", "extent"?}]; boxes: page -> [[x0, y0, x1, y1]].
    found     one box holds the view's point AND all its extent points (the whole view) and no other view's point
    coverable the boxes that hold some of its points and no other view's point TOGETHER hold all its points: the naming step can merge these
              fragments into the whole view (this is what the code must deliver; the model links fragments of one view, see extract.py)
    precision: boxes holding exactly one view point / all boxes. merged = boxes holding 2+ view points (two views in one crop: the one error
    the naming step cannot undo), empty = boxes holding none."""
    found = coverable = good = merged = empty = total = 0
    for page in {t["page"] for t in truth} | set(boxes):
        views = [t for t in truth if t["page"] == page]
        page_boxes = boxes.get(page, [])
        total += len(page_boxes)
        hold = [[v["point"] for v in views if _inside(v["point"], b)] for b in page_boxes]
        good += sum(1 for h in hold if len(h) == 1)
        merged += sum(1 for h in hold if len(h) > 1)
        empty += sum(1 for h in hold if not h)
        for v in views:
            pts = [v["point"], *v.get("extent", [])]
            if any(len(hold[i]) == 1 and all(_inside(p, b, _tol(b)) for p in pts) for i, b in enumerate(page_boxes)):
                found += 1
            own = [b for i, b in enumerate(page_boxes) if hold[i] in ([], [v["point"]]) and any(_inside(p, b, _tol(b)) for p in pts)]
            coverable += any(v["point"] in h for h in hold) and all(any(_inside(p, b, _tol(b)) for b in own) for p in pts)
    n = len(truth)
    return {"views": n, "boxes": total, "found": found, "recall": found / n if n else None, "coverable": coverable,
            "coverable_recall": coverable / n if n else None, "precision": good / total if total else 0.0, "merged": merged, "empty": empty}
