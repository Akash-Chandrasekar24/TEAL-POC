"""Pure geometry shared by the two revolve features (`revolve_on_face`, `revolve_base`): volumes by Pappus, section checks, the wall probe.

A section is a closed outline of lines and arcs {"kind": "line", x1, y1, x2, y2} / {"kind": "arc", cx, cy, x1, y1, x2, y2, dir} in the drawing's unit.
Revolving it about a LINE gives V = angle x (first moment of the section about that line): exact by Green's theorem for lines and arcs
(the same integral as `revolve_boss.revolve_volume`, which is the 360-degree volume about the vertical line x = 0).
"""
from __future__ import annotations

import math

from app.geometry import profile_area, profile_bounds
from .base import FeatureRequestError, Probe, POS_TOL_M
from .outline_helpers import check_simple_polygon
from .revolve_boss import EPS, _r_extent, _sample, revolve_volume


def shifted(profile: list[dict], dx: float = 0.0, dy: float = 0.0) -> list[dict]:
    out = []
    for s in profile:
        t = dict(s)
        for kx, ky in (("x1", "y1"), ("x2", "y2"), ("cx", "cy")):
            if kx in t:
                t[kx], t[ky] = t[kx] + dx, t[ky] + dy
        out.append(t)
    return out


def swapped(profile: list[dict]) -> list[dict]:
    """Swap x and y (a reflection in the line x = y): arcs change their sense. Used to turn a (u, r) half-section into the (r, h) form of `revolve_volume`."""
    out = []
    for s in profile:
        t = dict(s)
        for kx, ky in (("x1", "y1"), ("x2", "y2"), ("cx", "cy")):
            if kx in t:
                t[kx], t[ky] = s[ky], s[kx]
        if t["kind"] == "arc":
            t["dir"] = -s["dir"]
        out.append(t)
    return out


def volume_about_vertical(profile: list[dict], axis_x: float, angle_deg: float) -> float:
    """Volume (unit^3) swept by the section revolved `angle_deg` about the vertical line x = axis_x. The section must not straddle the line."""
    return revolve_volume(shifted(profile, dx=-axis_x)) * angle_deg / 360.0


def circle_section_volume(outer_d: float, bore_d: float | None, centre_distance: float, angle_deg: float) -> float:
    """A round (optionally bored) section whose centre is `centre_distance` from the axis: Pappus, area x path of its centroid."""
    area = math.pi / 4 * (outer_d ** 2 - (bore_d or 0.0) ** 2)
    return area * math.radians(angle_deg) * abs(centre_distance)


def check_section_loop(profile: list[dict], what: str) -> None:
    """A closed, simple, non-empty outline."""
    from .base import check_closed
    if not profile:
        raise FeatureRequestError(f"{what}: empty outline")
    for s in profile:
        if s.get("kind") not in ("line", "arc"):
            raise FeatureRequestError(f"{what}: segment kind {s.get('kind')!r} is not supported")
        if s["kind"] == "arc" and s.get("dir") not in (1, -1):
            raise FeatureRequestError(f"{what}: every arc needs dir = 1 or -1")
    check_closed(profile)
    try:
        check_simple_polygon(_sample(profile))
    except FeatureRequestError as ex:
        raise FeatureRequestError(f"{what}: the outline crosses or touches itself ({ex})") from ex
    if profile_area(profile) <= 0:
        raise FeatureRequestError(f"{what}: the outline encloses no area")


def side_of_vertical_axis(profile: list[dict], axis_x: float) -> int:
    """+1 / -1: the section lies right / left of the vertical line x = axis_x (touching it is allowed). Raises when it straddles the line."""
    x0, x1, _, _ = profile_bounds(profile)
    if x0 >= axis_x - EPS:
        return 1
    if x1 <= axis_x + EPS:
        return -1
    raise FeatureRequestError(f"the section crosses the axis (x from {x0:g} to {x1:g}, axis at {axis_x:g}): the revolved solid would overlap itself")


def axis_edges_vertical(profile: list[dict], axis_x: float) -> list[dict]:
    return [s for s in profile if s["kind"] == "line" and abs(s["x1"] - axis_x) < EPS and abs(s["x2"] - axis_x) < EPS and abs(s["y1"] - s["y2"]) > EPS]


def lowest_r(rh_profile: list[dict]) -> float:
    """Smallest r (x of an (r, h) profile) reached, arcs included."""
    return _r_extent(rh_profile)


# ------------------------------------------------------------------ probes
def probe_revolved_walls(faces: list[dict], radii_m: list[float], axis_index: int, perp: tuple[tuple[int, float], tuple[int, float]], name: str) -> Probe:
    """Every radius must exist as a cylinder face whose axis runs along world axis `axis_index` and whose axis line passes through the two
    perpendicular coordinates `perp` = ((world index, value m), (world index, value m)). (The sign of a reported axis vector is arbitrary.)"""
    (ia, va), (ib, vb) = perp
    have = [(c["radius"], c["origin"][ia], c["origin"][ib]) for c in faces
            if c.get("kind") == "cylinder" and abs(abs(c["axis"][axis_index]) - 1) < 1e-6]
    missing = [r for r in radii_m if not any(abs(h[0] - r) <= POS_TOL_M and abs(h[1] - va) <= POS_TOL_M and abs(h[2] - vb) <= POS_TOL_M for h in have)]
    return Probe(name, not missing, "all found" if not missing else
                 f"missing radii (mm) {[round(r * 1000, 3) for r in missing]} on the axis line; cylinders along this axis: "
                 f"{[tuple(round(v * 1000, 3) for v in h) for h in have]}")
