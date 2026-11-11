"""The elevation pass: one short, focused question after a build list is written. Does the part's SIDE view show a constant height?
If not, the list gets the base extruded to the full height and a side_profile_cut with the side outline. A rule among many in the long
reader prompt was followed only some of the time (a triple clamp came out as a flat slab); one question on its own is answered reliably.
Works for any extruded part: brackets with sloped arms, stepped plates, castings thicker in the middle."""
from __future__ import annotations

from app.llm.base import FilePart

PROMPT = """You look at ONE engineering drawing of a part that is modelled by extruding its TOP-view outline. Look at its SIDE view / elevation
(the view showing the part's length horizontally and its height vertically, often marked like "C").
First: is the part ONE plate-like piece made by extruding a top-view outline? A pipe, elbow, bend, tube, swept passage, a turned / round
part, or a part made of several pieces is NOT: then reply {"constant": true}.
Question: is the part's height the SAME everywhere along its length?
- If yes: reply {"constant": true}.
- If no (thick ends and a raised middle, sloped or stepped arms, a hump): reply {"constant": false, "full_height": H, "outline": [[x, y], ...],
  "corner_radius": [r, ...], "evidence": "..."} where the outline is the part's SIDE silhouette as a closed polygon walked counter-clockwise:
  x along the part measured from the LEFT end of the part exactly as in the top view (0 .. the part's length), y = height above the part's
  lowest face (0 .. H). Use the printed numbers (heights, lengths, angles, radii); put each printed radius on its corner (0 = sharp).
  full_height = the tallest printed height. Ignore holes, slots and splits: only the outer silhouette.
Reply with JSON only."""


def ask_elevation(llm, picture: bytes) -> dict | None:
    """The question alone: it needs only the drawing, so it runs WHILE the build list is written (saves one model call of waiting)."""
    reply = llm.generate_json(PROMPT, [FilePart(picture, "image/png")]).data
    return reply if isinstance(reply, dict) else None


def elevation_from(reply: dict | None, draft: dict, part_length: float) -> dict | None:
    """The side_profile_cut item to add (and the full height for the base), or None when the height is constant / the reply is unusable
    or does not fit the list's base (its length differs from the base's by more than 10 %: a different reading of the part)."""
    items = draft.get("items") or []
    if not items or items[0].get("feature") != "extrude_profile_boss" or any(i.get("feature") == "side_profile_cut" for i in items):
        return None
    if not isinstance(reply, dict) or reply.get("constant") is not False:
        return None
    pts, h = reply.get("outline"), reply.get("full_height")
    if not (isinstance(pts, list) and len(pts) >= 3 and isinstance(h, (int, float)) and h > 0):
        return None
    xs = [float(p[0]) for p in pts]
    span = max(xs) - min(xs)
    if not part_length or span <= 0 or abs(span / part_length - 1) > 0.10:
        return None
    k = part_length / span                                                      # the base's length wins: the cut then meets both ends exactly
    radii = reply.get("corner_radius")
    outline = {"shape": "polygon", "frame": "base_lower_left", "points": [[(float(p[0]) - min(xs)) * k, float(p[1])] for p in pts]}
    if isinstance(radii, list) and len(radii) == len(pts):
        outline["corner_radius"] = [float(r or 0) for r in radii]
    return {"full_height": float(h), "item": {"id": "E1", "feature": "side_profile_cut", "printed": "side view (elevation)",
                                              "derived": True, "derived_from": "elevation pass: " + str(reply.get("evidence", ""))[:300],
                                              "params": {"outline": outline}}}


def apply_elevation(draft: dict, found: dict) -> dict:
    """The list with the base extruded to the full height and the side profile cut right after it."""
    items = [dict(i) for i in draft["items"]]
    base = dict(items[0]); base["params"] = {**base["params"], "depth": found["full_height"]}
    base["derived"] = True
    base["derived_from"] = (base.get("derived_from", "") + " | depth = the full height from the side view").strip(" |")
    return {**draft, "items": [base, found["item"], *items[1:]]}
