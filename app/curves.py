"""Exact engineering curves as closed outlines of lines and arcs (drawing unit, centred on the origin).

Used for parts whose outline is defined by a formula and a few printed numbers rather than by printed points:
  - involute spur gears, external and internal (teeth, module, pressure angle)       e.g. "z = 42, m = 2.00, alpha = 20 deg"
  - the two-lobe epitrochoid bore of a Wankel rotor housing (generating radius R, eccentricity e)
  - the three-flank Wankel rotor, the inner envelope of that epitrochoid

Circular parts of a curve (gear tip and root circles) are true arcs. Non-circular parts (involute flanks, trochoid, rotor flank) are sampled densely
and reduced to straight chords that stay within `tol` of the curve (Ramer-Douglas-Peucker on the dense samples). The chord tolerance is a stated
modelling tolerance in the drawing's unit (default 0.005 = 5 micrometres in mm), far below any printed manufacturing tolerance; it is reported with
every outline. What is NOT modelled is said explicitly by the caller (for example gear root fillets).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

MAX_SEGMENTS = 6000          # a sketch larger than this is refused: split the part or relax the tolerance


class CurveError(ValueError):
    pass


@dataclass(frozen=True)
class Outline:
    segments: list[dict]       # closed, counter-clockwise, consecutive segments share end points exactly
    tolerance: float           # largest chord deviation from the true curve (drawing unit)
    notes: tuple[str, ...] = ()


# ------------------------------------------------------------------ helpers
def _rdp(points: np.ndarray, tol: float) -> list[int]:
    """Indices of an open polyline kept by Ramer-Douglas-Peucker (first and last always kept)."""
    keep = [0, len(points) - 1]
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = points[i], points[j]
        ab = b - a
        n = float(np.hypot(*ab))
        seg = points[i + 1:j]
        if n == 0:
            d = np.hypot(*(seg - a).T)
        else:
            d = np.abs(ab[0] * (seg[:, 1] - a[1]) - ab[1] * (seg[:, 0] - a[0])) / n
        k = int(np.argmax(d))
        if d[k] > tol:
            m = i + 1 + k
            keep.append(m)
            stack += [(i, m), (m, j)]
    return sorted(set(keep))


def _chords(points: np.ndarray, tol: float) -> list[tuple[float, float]]:
    """An open dense polyline -> its reduced vertices (start and end exactly kept)."""
    return [(float(points[i, 0]), float(points[i, 1])) for i in _rdp(points, tol)]


def _closed_chords(points: np.ndarray, tol: float) -> list[tuple[float, float]]:
    """A closed dense curve (no repeated end point) -> reduced vertices. Split at the two farthest-apart samples so RDP sees two open halves."""
    k = int(np.argmax(np.hypot(*(points - points[0]).T)))
    a = _chords(np.vstack([points[:k + 1]]), tol)
    b = _chords(np.vstack([points[k:], points[:1]]), tol)
    return a + b[1:-1]


def _lines(vertices: list[tuple[float, float]], closed: bool) -> list[dict]:
    pts = vertices + ([vertices[0]] if closed else [])
    return [{"kind": "line", "x1": p[0], "y1": p[1], "x2": q[0], "y2": q[1]} for p, q in zip(pts, pts[1:]) if p != q]


def _arc(cx, cy, r, a1, a2) -> dict:
    """Counter-clockwise arc from angle a1 to a2."""
    return {"kind": "arc", "cx": cx, "cy": cy, "x1": cx + r * math.cos(a1), "y1": cy + r * math.sin(a1),
            "x2": cx + r * math.cos(a2), "y2": cy + r * math.sin(a2), "dir": 1}


def _place(segs: list[dict], dx: float, dy: float, rot_deg: float) -> list[dict]:
    """Rotate about the origin, then move. Exact end points are kept by applying the same map to every coordinate pair."""
    c, s = math.cos(math.radians(rot_deg)), math.sin(math.radians(rot_deg))
    out = []
    for g in segs:
        h = dict(g)
        for x, y in (("x1", "y1"), ("x2", "y2"), ("cx", "cy")):
            if x in g:
                h[x], h[y] = g[x] * c - g[y] * s + dx, g[x] * s + g[y] * c + dy
        out.append(h)
    return out


def _stitch(segs: list[dict]) -> list[dict]:
    """Make consecutive end points bit-identical (they are equal up to rounding where an arc meets a chord)."""
    for a, b in zip(segs, segs[1:] + segs[:1]):
        if math.hypot(a["x2"] - b["x1"], a["y2"] - b["y1"]) > 1e-6:
            raise CurveError("internal: generated outline is not closed")
        b["x1"], b["y1"] = a["x2"], a["y2"]
    return segs


def _check(segs: list[dict]) -> list[dict]:
    if len(segs) > MAX_SEGMENTS:
        raise CurveError(f"the outline needs {len(segs)} sketch segments (limit {MAX_SEGMENTS}); relax chord_tolerance or split the feature")
    return _stitch(segs)


# ------------------------------------------------------------------ involute spur gears
def inv(a: float) -> float:
    return math.tan(a) - a


@dataclass(frozen=True)
class GearSize:
    pitch: float           # pitch diameter  m z
    base: float            # base diameter   m z cos(alpha)
    tip: float             # external: outside diameter; internal: the INNER (tooth-tip) diameter
    root: float            # external: root diameter; internal: the OUTER (space-bottom) diameter


def gear_size(teeth: int, module: float, pressure_deg: float = 20.0, internal: bool = False,
              addendum: float = 1.0, dedendum: float = 1.25) -> GearSize:
    d = module * teeth
    db = d * math.cos(math.radians(pressure_deg))
    if internal:
        return GearSize(d, db, d - 2 * addendum * module, d + 2 * dedendum * module)
    return GearSize(d, db, d + 2 * addendum * module, d - 2 * dedendum * module)


def spur_gear(teeth: int, module: float, pressure_deg: float = 20.0, internal: bool = False, *, tol: float = 0.005,
              addendum: float = 1.0, dedendum: float = 1.25, x: float = 0.0, y: float = 0.0, rotation_deg: float = 0.0) -> Outline:
    """Standard full-depth involute gear, no profile shift, no backlash (tooth = space = pi m / 2 on the pitch circle).
    External: the outline of the gear body (teeth outward). Internal: the outline of the toothed HOLE (teeth point inward)."""
    if teeth < 6 or not module > 0 or not 10 <= pressure_deg <= 35 or not tol > 0:
        raise CurveError("a gear needs at least 6 teeth, a positive module, a pressure angle of 10..35 deg and a positive tolerance")
    size = gear_size(teeth, module, pressure_deg, internal, addendum, dedendum)
    r, rb = size.pitch / 2, size.base / 2
    alpha = math.radians(pressure_deg)
    psi = math.pi / (2 * teeth)                                   # half the tooth (external) / space (internal) angle on the pitch circle
    r_lo, r_hi = (size.tip / 2, size.root / 2) if internal else (size.root / 2, size.tip / 2)   # the flank runs between these radii
    notes = []
    if r_lo < rb:
        if internal:
            raise CurveError("internal gear: the tooth tips reach inside the base circle (too few teeth for this addendum)")
        notes.append("Below the base circle the flank is a radial line (no involute exists there).")

    def half(rr):                                                  # half angular width of the tooth / space body at radius rr
        return psi + inv(alpha) - (inv(math.acos(rb / rr)) if rr > rb else 0.0)

    if half(r_hi) <= 0:
        raise CurveError("the teeth come to a point before the tip circle: check teeth, module and addendum")
    pitch_angle = 2 * math.pi / teeth
    if pitch_angle - 2 * half(r_lo) <= 0:
        raise CurveError("adjacent teeth overlap at the root circle")
    radii = np.linspace(r_lo, r_hi, 400)
    flank = np.array([[rr * math.cos(-half(rr)), rr * math.sin(-half(rr))] for rr in radii])     # the lower flank of the body centred on +x
    up = _chords(flank, tol)                                      # r_lo -> r_hi, lower flank
    down = [(px, -py) for px, py in reversed(up)]                 # r_hi -> r_lo, upper flank (mirror)
    segs: list[dict] = []
    for k in range(teeth):
        c = k * pitch_angle
        rot = lambda pts: [(px * math.cos(c) - py * math.sin(c), px * math.sin(c) + py * math.cos(c)) for px, py in pts]
        segs += _lines(rot(up), False)
        segs.append(_arc(0, 0, r_hi, c - half(r_hi), c + half(r_hi)))          # tip (external) / space bottom (internal)
        segs += _lines(rot(down), False)
        segs.append(_arc(0, 0, r_lo, c + half(r_lo), c + pitch_angle - half(r_lo)))   # root (external) / tooth tip (internal)
    notes.append(f"{'Internal' if internal else 'External'} involute gear z={teeth} m={module:g} alpha={pressure_deg:g} deg: "
                 f"pitch {size.pitch:g}, base {size.base:.3f}, {'tip (inner)' if internal else 'tip'} {size.tip:g}, root {size.root:g}. "
                 "Root fillets are not modelled.")
    return Outline(_check(_place(segs, x, y, rotation_deg)), tol, tuple(notes))


# ------------------------------------------------------------------ Wankel: housing trochoid and rotor
def epitrochoid_points(R: float, e: float, n: int = 8192, offset: float = 0.0) -> np.ndarray:
    """Two-lobe epitrochoid x = R cos t + e cos 3t, y = R sin t + e sin 3t (major axis along x), moved outward by `offset` along its normal."""
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    p = np.stack([R * np.cos(t) + e * np.cos(3 * t), R * np.sin(t) + e * np.sin(3 * t)], axis=1)
    if offset:
        d = np.stack([-R * np.sin(t) - 3 * e * np.sin(3 * t), R * np.cos(t) + 3 * e * np.cos(3 * t)], axis=1)
        normal = np.stack([d[:, 1], -d[:, 0]], axis=1) / np.hypot(*d.T)[:, None]       # outward for a counter-clockwise curve
        p = p + offset * normal
    return p


def _wankel_checks(R: float, e: float, tol: float):
    if not (R > 0 and e > 0 and tol > 0):
        raise CurveError("generating radius, eccentricity and tolerance must be positive")
    if R / e < 3:
        raise CurveError(f"R/e = {R / e:.2f}: a two-lobe trochoid needs R/e > 3 (it would loop on itself)")


def epitrochoid(R: float, e: float, *, clearance: float = 0.0, tol: float = 0.005, x: float = 0.0, y: float = 0.0, rotation_deg: float = 0.0) -> Outline:
    """The rotor-housing bore: the epitrochoid traced by the rotor apexes, optionally enlarged by an apex-seal clearance."""
    _wankel_checks(R, e, tol)
    pts = epitrochoid_points(R, e, offset=clearance)
    segs = _lines(_closed_chords(pts, tol), True)
    notes = (f"Two-lobe epitrochoid R={R:g} e={e:g}{f' + clearance {clearance:g}' if clearance else ''}: "
             f"major axis {2 * (R + e + clearance):g}, minor axis {2 * (R - e + clearance):g} (along x before rotation).",)
    return Outline(_check(_place(segs, x, y, rotation_deg)), tol, notes)


def rotor_points(R: float, e: float, n_theta: int = 7200, n_phi: int = 2880, n_t: int = 8192, clearance: float = 0.0) -> np.ndarray:
    """Polar inner envelope of the housing trochoid as seen from the rotor (rotor centre at the origin, an apex on +x).
    The rotor turns phi while the eccentric turns 3 phi; in the rotor frame every housing position is a closed curve around the origin, and the
    rotor flank is the smallest radius over all positions in each direction. Returns dense points (n_theta, 2)."""
    t = np.linspace(0, 2 * np.pi, n_t, endpoint=False)
    hx, hy = R * np.cos(t) + e * np.cos(3 * t), R * np.sin(t) + e * np.sin(3 * t)
    theta = np.linspace(-np.pi, np.pi, n_theta, endpoint=False)
    rho = np.full(n_theta, np.inf)
    for phi in np.linspace(0, 2 * np.pi, n_phi, endpoint=False):
        px, py = hx - e * np.cos(3 * phi), hy - e * np.sin(3 * phi)
        c, s = np.cos(-phi), np.sin(-phi)
        qx, qy = c * px - s * py, s * px + c * py
        ang, rad = np.arctan2(qy, qx), np.hypot(qx, qy)
        order = np.argsort(ang)
        ang, rad = ang[order], rad[order]
        ang = np.concatenate([ang[-1:] - 2 * np.pi, ang, ang[:1] + 2 * np.pi])
        rad = np.concatenate([rad[-1:], rad, rad[:1]])
        rho = np.minimum(rho, np.interp(theta, ang, rad))
    rho = rho - clearance
    return np.stack([rho * np.cos(theta), rho * np.sin(theta)], axis=1)


@lru_cache(maxsize=16)
def _rotor_vertices(R: float, e: float, clearance: float, tol: float) -> tuple:
    """The envelope takes a few seconds: the same rotor is computed once per process."""
    return tuple(_closed_chords(rotor_points(R, e, clearance=clearance), tol))


def rotor_flank(R: float, e: float, *, clearance: float = 0.0, tol: float = 0.005, x: float = 0.0, y: float = 0.0, rotation_deg: float = 0.0) -> Outline:
    """The three-flank rotor that fits the housing trochoid (apexes on the generating radius R), optionally reduced by a running clearance."""
    _wankel_checks(R, e, tol)
    segs = _lines(list(_rotor_vertices(float(R), float(e), float(clearance), float(tol))), True)
    notes = (f"Rotor = inner envelope of the R={R:g}, e={e:g} trochoid{f' - clearance {clearance:g}' if clearance else ''}: "
             f"apexes on radius {R - clearance:g}, apex-to-apex {math.sqrt(3) * (R - clearance):.3f}. Apex-seal grooves are separate features.",)
    return Outline(_check(_place(segs, x, y, rotation_deg)), tol, notes)


def wankel_consistency(R: float, e: float, printed: dict) -> list[str]:
    """Compare what R and e imply with dimensions printed elsewhere on the sheet. Returns the disagreements (empty = consistent).
    printed keys (all optional): rotor_apex_distance, housing_major_axis, housing_minor_axis; tolerance: 'tol' (default 0.5)."""
    tol = printed.get("tol", 0.5)
    implied = {"rotor_apex_distance": math.sqrt(3) * R, "housing_major_axis": 2 * (R + e), "housing_minor_axis": 2 * (R - e)}
    names = {"rotor_apex_distance": "rotor apex-to-apex width", "housing_major_axis": "housing bore major axis", "housing_minor_axis": "housing bore minor axis"}
    return [f"{names[k]}: R={R:g}, e={e:g} give {v:.2f} but the drawing prints {printed[k]:g}"
            for k, v in implied.items() if k in printed and abs(printed[k] - v) > tol]
