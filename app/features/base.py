"""Shared pieces for library features.

Request models are written the way the drawing reader fills them in:
the DRAWING's unit (mm or inch), positions as printed (`x` + `x_from`, `y` + `y_from`, the reader's own words),
and the conversion to metres happens INSIDE the feature, never in the caller.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.mcp_client import SolidWorksClient
from app.geometry import profile_area, profile_bounds, profile_vertex_bounds
from app.verify import RunLog, StepVerdict, StepVerifier

M_PER_UNIT = {"mm": 0.001, "inch": 0.0254}
POS_TOL_M = 2.5e-6          # SolidWorks reports 6 decimals of a metre
AREA_TOL_M2 = 1.5e-6


class FeatureRequestError(ValueError):
    """The request itself is wrong (bad unit, unsupported plane, missing size...). Nothing was sent to SolidWorks."""


def factor(unit: str) -> float:
    if unit not in M_PER_UNIT:
        raise FeatureRequestError(f"unit must be 'mm' or 'inch', got {unit!r}")
    return M_PER_UNIT[unit]


# ------------------------------------------------------------------ positions as printed
@dataclass(frozen=True)
class Position:
    """A point as the drawing prints it. `x_from`: left_edge | right_edge | centre. `y_from`: bottom_edge | top_edge | centre.
    Edge words need the size of the base outline (`outline_wh`); `centre` means the sketch origin."""
    x: float
    y: float
    x_from: str = "centre"
    y_from: str = "centre"

    def to_sketch_m(self, unit: str, outline_wh: tuple[float, float] | None = None) -> tuple[float, float]:
        f = factor(unit)
        if (self.x_from != "centre" or self.y_from != "centre") and outline_wh is None:
            raise FeatureRequestError("positions measured from an edge need the base outline size (outline_wh)")
        W, H = outline_wh or (0.0, 0.0)
        xs = {"left_edge": self.x - W / 2, "right_edge": W / 2 - self.x, "centre": self.x}
        ys = {"bottom_edge": self.y - H / 2, "top_edge": H / 2 - self.y, "centre": self.y}
        if self.x_from not in xs or self.y_from not in ys:
            raise FeatureRequestError(f"bad x_from/y_from: {self.x_from!r}/{self.y_from!r}")
        return xs[self.x_from] * f, ys[self.y_from] * f


# ------------------------------------------------------------------ datum planes (frames measured in SolidWorks)
# datum -> (world axis of sketch x, its sign), (world axis of sketch y, its sign), world axis of the normal (always the + direction)
_FRAMES = {"front": ((0, 1), (1, 1), 2), "top": ((0, 1), (2, -1), 1), "right": ((2, -1), (1, 1), 0)}


@dataclass(frozen=True)
class PlaneFrame:
    """Sketch -> world mapping of a datum plane (all measured with create_sketch's frame readback).
    Front: sketch x,y = world X,Y, normal +Z.  Top: sketch x = world X, y = world -Z, normal +Y.  Right: sketch x = world -Z, y = world Y, normal +X."""
    datum: str

    def __post_init__(self):
        if self.datum not in _FRAMES:
            raise FeatureRequestError(f"plane must be 'front', 'top' or 'right', got {self.datum!r}")

    @property
    def normal_axis(self) -> int:           # index into (X, Y, Z)
        return _FRAMES[self.datum][2]

    @property
    def in_plane_axes(self) -> tuple[int, int]:
        """World axes that the sketch x and y run along."""
        return _FRAMES[self.datum][0][0], _FRAMES[self.datum][1][0]

    @property
    def signs(self) -> tuple[int, int]:
        """+1 / -1: whether the sketch x / y run with (+) or against (-) their world axes."""
        return _FRAMES[self.datum][0][1], _FRAMES[self.datum][1][1]

    def to_world(self, x: float, y: float, d: float) -> tuple[float, float, float]:
        """World point of sketch point (x, y) at distance d along the datum normal."""
        w = [0.0, 0.0, 0.0]
        (ax, sx), (ay, sy), n = _FRAMES[self.datum]
        w[ax], w[ay], w[n] = sx * x, sy * y, d
        return w[0], w[1], w[2]

    def in_plane_world(self, x: float, y: float) -> tuple[float, float]:
        w = self.to_world(x, y, 0.0)
        a, b = self.in_plane_axes
        return w[a], w[b]


# ------------------------------------------------------------------ profiles (drawing unit -> graph metres)
def profile_to_graph(profile: list[dict], unit: str, origin: str = "centre",
                     size: tuple[float, float] | None = None) -> list[dict]:
    """Segments in the drawing's unit -> graph segments in metres. origin 'lower_left' = the reader's profile frame
    (shifted by W/2, H/2, as the builder skill does); 'centre' = coordinates are already measured from the sketch origin."""
    f = factor(unit)
    dx = dy = 0.0
    if origin == "lower_left":
        W, H = size if size else _size_of(profile)
        dx, dy = W / 2, H / 2
    elif origin != "centre":
        raise FeatureRequestError(f"profile origin must be 'centre' or 'lower_left', got {origin!r}")
    out = []
    for s in profile:
        if s["kind"] == "circle":                                          # a circle or a RING (two concentric circles) in one sketch
            out.append({"kind": "circle", "diameter": s["diameter"] * f, "cx": (s["cx"] - dx) * f, "cy": (s["cy"] - dy) * f})
        elif s["kind"] == "line":
            out.append({"kind": "line", "x1": (s["x1"] - dx) * f, "y1": (s["y1"] - dy) * f,
                        "x2": (s["x2"] - dx) * f, "y2": (s["y2"] - dy) * f})
        elif s["kind"] == "arc":
            if s.get("dir") not in (1, -1):
                raise FeatureRequestError("every arc needs dir = 1 (counter-clockwise) or -1 (clockwise)")
            out.append({"kind": "arc", "cx": (s["cx"] - dx) * f, "cy": (s["cy"] - dy) * f,
                        "x1": (s["x1"] - dx) * f, "y1": (s["y1"] - dy) * f,
                        "x2": (s["x2"] - dx) * f, "y2": (s["y2"] - dy) * f, "dir": s["dir"]})
        else:
            raise FeatureRequestError(f"profile segment kind {s.get('kind')!r} is not supported here")
    return out


def _size_of(profile: list[dict]) -> tuple[float, float]:
    x0, x1, y0, y1 = profile_bounds(profile)
    return x1 - x0, y1 - y0


def check_ring(profile: list[dict]) -> None:
    """A profile of circles is ONE circle or a RING: two concentric circles, the inner one strictly smaller."""
    if len(profile) > 2:
        raise FeatureRequestError("a profile of circles is one circle or a ring (two concentric circles), not more")
    if any(s["diameter"] <= 0 for s in profile):
        raise FeatureRequestError("circle diameters must be positive")
    if len(profile) == 2:
        a, b = profile                                                     # the OUTER circle comes first, the inner one second: never reorder, refuse a swapped pair
        if abs(a["cx"] - b["cx"]) > 1e-9 or abs(a["cy"] - b["cy"]) > 1e-9:
            raise FeatureRequestError("the two circles of a ring must be concentric")
        if b["diameter"] >= a["diameter"] - 1e-12:
            raise FeatureRequestError(f"the inner diameter of a ring ({b['diameter']:g}) must be smaller than the outer diameter ({a['diameter']:g})")


def check_closed(profile: list[dict], tol: float = 1e-9) -> None:
    if profile and all(s.get("kind") == "circle" for s in profile):
        check_ring(profile)                                                # circles are closed by themselves
        return
    if any(s.get("kind") == "circle" for s in profile):
        raise FeatureRequestError("circles cannot be mixed with lines and arcs in one profile")
    for a, b in zip(profile, profile[1:] + profile[:1]):
        if abs(a["x2"] - b["x1"]) > tol or abs(a["y2"] - b["y1"]) > tol:
            raise FeatureRequestError("the outline is not closed: each segment must start exactly where the previous one ends")


def circle_node(diameter: float, cx_m: float, cy_m: float, unit: str) -> dict:
    return {"kind": "circle", "diameter": diameter * factor(unit), "cx": cx_m, "cy": cy_m}


def sketch_node(nid: str, datum: str, offset_m: float, profile: list[dict]) -> dict:
    ref: dict[str, Any] = {"datum": datum}
    if abs(offset_m) > 1e-12:
        ref["offset"] = offset_m
    return {"id": nid, "type": "sketch", "ref": ref, "profile": profile}


def graph_of(nodes: list[dict]) -> dict:
    return {"schema_version": "0.7.2-draft", "units": "meters", "nodes": nodes}


# ------------------------------------------------------------------ probes (position checks; volume cannot see mirrors)
@dataclass
class Probe:
    name: str
    ok: bool
    detail: str = ""


async def read_geometry(sw: SolidWorksClient) -> tuple[list[dict], list[dict]]:
    """(faces, edges) of the active part. NOTE: analyze_model(edges) lists no partial-arc edges (measured)."""
    fa, ed = await sw.analyze_model("faces"), await sw.analyze_model("edges")
    if not (fa.ok and ed.ok):
        raise RuntimeError(f"cannot read geometry: {(fa.text if not fa.ok else ed.text)[:200]}")
    return fa.analysis_json()["faces"], ed.analysis_json()["edges"]


def probe_axis_extent(edges: list[dict], axis: int, lo_m: float, hi_m: float, name: str) -> Probe:
    """Extent of the part along one world axis, from edge end/mid points (exact along an extrusion axis)."""
    vals = [p[axis] for e in edges for p in (e["start"], e["end"], e["mid"])]
    got = (min(vals), max(vals))
    ok = abs(got[0] - lo_m) <= POS_TOL_M and abs(got[1] - hi_m) <= POS_TOL_M
    return Probe(name, ok, f"expected {lo_m * 1000:.3f} .. {hi_m * 1000:.3f} mm, got {got[0] * 1000:.3f} .. {got[1] * 1000:.3f} mm")


def probe_axis_extent_range(edges: list[dict], axis: int, seen_lo: float, seen_hi: float, full_lo: float, full_hi: float,
                            name: str) -> Probe:
    """In-plane extent where an arc may bulge past the end points: min must lie in [full_lo, seen_lo], max in [seen_hi, full_hi]."""
    vals = [p[axis] for e in edges for p in (e["start"], e["end"], e["mid"])]
    lo, hi = min(vals), max(vals)
    ok = full_lo - POS_TOL_M <= lo <= seen_lo + POS_TOL_M and seen_hi - POS_TOL_M <= hi <= full_hi + POS_TOL_M
    return Probe(name, ok, f"got {lo * 1000:.3f} .. {hi * 1000:.3f} mm; allowed min in [{full_lo * 1000:.3f}, {seen_lo * 1000:.3f}], "
                           f"max in [{seen_hi * 1000:.3f}, {full_hi * 1000:.3f}] mm")


def probe_bbox(faces: list[dict], edges: list[dict], lo_m: tuple[float, float, float], hi_m: tuple[float, float, float],
               name: str, tol_m: float = POS_TOL_M) -> Probe:
    """Exact world bounding box (edges + cylinder faces, see app.verify.bbox) against the expected corners."""
    from app.verify.bbox import exact_bbox
    bb = exact_bbox(faces, edges)
    bad = [f"{'xyz'[i]}: {bb.lo[i] * 1000:.3f}..{bb.hi[i] * 1000:.3f} mm, expected {lo_m[i] * 1000:.3f}..{hi_m[i] * 1000:.3f} mm"
           for i in range(3) if abs(bb.lo[i] - lo_m[i]) > tol_m or abs(bb.hi[i] - hi_m[i]) > tol_m]
    note = f" (uncertain faces: {bb.uncertain})" if bb.uncertain else ""
    return Probe(name, not bad and not bb.uncertain, ("matches: " + ", ".join(f"{'xyz'[i]} {bb.lo[i] * 1000:.3f}..{bb.hi[i] * 1000:.3f}" for i in range(3)) + " mm" if not bad else "; ".join(bad)) + note)


def probe_cylinders(faces: list[dict], expected: list[tuple[float, float, float]], frame: PlaneFrame, name: str) -> Probe:
    """Every expected (radius_m, in-plane world a, b) must exist as a cylinder face whose axis is the plane normal.
    Extra cylinders are allowed (other features). Position is compared in the two in-plane world coordinates."""
    a_ax, b_ax = frame.in_plane_axes
    axis_idx = frame.normal_axis
    have = [(c["radius"], c["origin"][a_ax], c["origin"][b_ax]) for c in faces
            if c.get("kind") == "cylinder" and abs(abs(c["axis"][axis_idx]) - 1) < 1e-6]
    missing = [e for e in expected
               if not any(abs(e[0] - h[0]) <= POS_TOL_M and abs(e[1] - h[1]) <= POS_TOL_M and abs(e[2] - h[2]) <= POS_TOL_M for h in have)]
    names = ("xyz"[a_ax], "xyz"[b_ax])
    measured = [f"R{h[0] * 1000:.3f} axis at {names[0]}={h[1] * 1000:.3f}, {names[1]}={h[2] * 1000:.3f} mm"
                for e in expected if not (e in missing)
                for h in [min(have, key=lambda h: abs(e[0] - h[0]) + abs(e[1] - h[1]) + abs(e[2] - h[2]))]]
    return Probe(name, not missing, ("all found; measured: " + "; ".join(measured)) if not missing else
                 f"missing (r, a, b) in mm: {[tuple(round(v * 1000, 3) for v in m) for m in missing]}; "
                 f"cylinders on this axis ({names[0]}, {names[1]}): {[tuple(round(v * 1000, 3) for v in h) for h in have]}")


def probe_planar_face(faces: list[dict], axis: int, at_m: float, area_m2: float, name: str) -> Probe:
    """A planar face normal to `axis`, lying at `at_m`, with the given area."""
    hit = [p for p in faces if p.get("planar") and abs(abs(p["normal"][axis]) - 1) < 1e-3
           and abs(p["point"][axis] - at_m) <= POS_TOL_M and abs(p["area"] - area_m2) <= AREA_TOL_M2]
    near = [(round(p["point"][axis] * 1000, 3), round(p["area"] * 1e6, 1)) for p in faces
            if p.get("planar") and abs(abs(p["normal"][axis]) - 1) < 1e-3 and abs(p["point"][axis] - at_m) <= 1e-3]
    measured = (f"; measured plane {'xyz'[axis]}={hit[0]['point'][axis] * 1000:.3f} mm, area {hit[0]['area'] * 1e6:.1f} mm2") if hit else ""
    return Probe(name, bool(hit), f"expected face at {at_m * 1000:.3f} mm, {area_m2 * 1e6:.1f} mm2; found there: {near}{measured}")


def probe_segments(edges: list[dict], segments_m: list[dict], frame: PlaneFrame, name: str) -> Probe:
    """Each LINE of the profile must exist as an edge with those in-plane end points (the third coordinate is free)."""
    a_ax, b_ax = frame.in_plane_axes
    missing = []
    for s in segments_m:
        if s["kind"] != "line":
            continue
        p1, p2 = frame.in_plane_world(s["x1"], s["y1"]), frame.in_plane_world(s["x2"], s["y2"])
        found = any(
            (abs(e["start"][a_ax] - p1[0]) <= POS_TOL_M and abs(e["start"][b_ax] - p1[1]) <= POS_TOL_M
             and abs(e["end"][a_ax] - p2[0]) <= POS_TOL_M and abs(e["end"][b_ax] - p2[1]) <= POS_TOL_M)
            or (abs(e["start"][a_ax] - p2[0]) <= POS_TOL_M and abs(e["start"][b_ax] - p2[1]) <= POS_TOL_M
                and abs(e["end"][a_ax] - p1[0]) <= POS_TOL_M and abs(e["end"][b_ax] - p1[1]) <= POS_TOL_M)
            for e in edges)
        if not found:
            missing.append((p1, p2))
    return Probe(name, not missing, "all line edges found" if not missing else f"no edge for {len(missing)} line(s), first {missing[0]}")


# ------------------------------------------------------------------ running a feature
@dataclass
class FeatureContext:
    sw: SolidWorksClient
    verifier: StepVerifier
    log: RunLog | None = None


@dataclass
class FeatureResult:
    feature: str
    verdicts: list[StepVerdict] = field(default_factory=list)
    probes: list[Probe] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.verdicts) and all(v.ok for v in self.verdicts) and all(p.ok for p in self.probes)

    def lines(self) -> list[str]:
        out = [f"{self.feature}: {'OK' if self.ok else 'FAILED'}"]
        for v in self.verdicts:
            out.append(f"  step {v.name}: {v.verdict} | {v.message}")
            if v.regression:
                out.append(f"    regression vs reference: {v.regression}")
            for w in v.warnings:
                out.append(f"    warning: {w}")
        for p in self.probes:
            out.append(f"  probe [{'PASS' if p.ok else 'FAIL'}] {p.name}: {p.detail}")
        out += [f"  note: {n}" for n in self.notes]
        return out


def ring_area(d_outer: float, d_inner: float) -> float:
    return math.pi / 4 * (d_outer ** 2 - d_inner ** 2)
