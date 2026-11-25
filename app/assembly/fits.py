"""How the parts of one drawing fit together, read once per drawing, then used three times:

1. each part's build list is told its side of every fit (a bore of the shared diameter, a groove of the ring's width, room for a guest part);
2. before the assembly, every built part is measured against the fits (its round faces from SolidWorks): a missing bore, a wrong diameter or a
   groove narrower than its ring is sent to the SolidWorks agent as a correction of THAT part, then the part is measured again;
3. after the assembly, an overlap between two parts that fit together, where no placement can help (a cavity fit, or the same pair overlapping
   again after the placement was redone), is a correction of the host part.

A fit: the HOST holds, carries or surrounds the GUEST. kind "round" = a guest cylinder in a host bore (shared diameter); "groove" = a ring in a
host groove (diameter = groove bottom, width = groove width); "cavity" = the guest needs empty room inside the host that is not a plain bore;
"face" = two flat faces seated or bolted together.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PROMPT = """You are a mechanical design engineer reading ONE assembly drawing (the picture: every view, numbered). The parts on it are listed
below with their ids. List every FIT between two parts: where one part (the HOST) holds, carries or surrounds another (the GUEST).
Kinds:
- "round": a cylinder of the guest sits in a bore of the host (a pin in a piston's pin bore, the same pin in a rod's small-end bore, a crankpin
  in a rod's big-end bore and its cap, a shaft in a bearing, a bush in a housing). "diameter": the shared diameter.
- "groove": a ring sits in a groove of the host (piston rings, circlips, O-rings). "diameter": the ring's INSIDE diameter (the groove's
  bottom may be smaller: a ring sits with clearance behind it); "width": the groove width (the ring's axial thickness fits it).
- "cavity": the guest needs empty room inside the host that is not a plain bore (a rod's small end between a piston's pin bosses, inside its
  skirt; a gear inside a housing). "width" and/or "diameter": the size of the guest that must fit.
- "face": two flat faces seated or bolted together (a cap on its rod). No size.
Use printed values; a value you derived from others says "derived" in its evidence. Only fits the drawing shows; one entry per fit (not per
copy). Return JSON only: {"fits": [{"host": "<part id>", "host_feature": "...", "guest": "<part id>", "guest_feature": "...",
"kind": "round|groove|cavity|face", "diameter": mm or null, "width": mm or null, "evidence": "view and printed value"}]}"""


class Fit(BaseModel):
    model_config = ConfigDict(extra="ignore")
    host: str
    host_feature: str = ""
    guest: str
    guest_feature: str = ""
    kind: Literal["round", "groove", "cavity", "face"]
    diameter: float | None = Field(None, gt=0)
    width: float | None = Field(None, gt=0)
    evidence: str = ""


def parse_fits(data, part_ids) -> list[dict]:
    """The model's reply -> valid fits between two DIFFERENT known parts (anything else is dropped, not guessed)."""
    known, out = set(part_ids), []
    for raw in (data or {}).get("fits", []) if isinstance(data, dict) else []:
        try:
            fit = Fit.model_validate(raw)
        except ValidationError:
            continue
        if fit.host in known and fit.guest in known and fit.host != fit.guest:
            out.append(fit.model_dump())
    return out


def size_text(fit: dict) -> str:
    bits = ([f"diameter {fit['diameter']:g} mm"] if fit.get("diameter") else []) + ([f"width {fit['width']:g} mm"] if fit.get("width") else [])
    return ", ".join(bits) or "no size printed"


def fits_text(fits: list[dict], part_id: str, names: dict[str, str]) -> str:
    """For one part's build list: its side of every fit, with the shared sizes."""
    lines = []
    for f in fits:
        if f["host"] == part_id:
            lines.append(f"- it HOLDS {names.get(f['guest'], f['guest'])} ({f['guest_feature'] or 'its fitting part'}) in its "
                         f"{f['host_feature'] or 'seat'}: {f['kind']}, {size_text(f)}. Build that {f['kind'] if f['kind'] != 'round' else 'bore'} "
                         f"with this size{'; the room must be empty, the guest moves in it' if f['kind'] == 'cavity' else ''}.")
        elif f["guest"] == part_id:
            lines.append(f"- it SITS in {names.get(f['host'], f['host'])} ({f['host_feature'] or 'its seat'}) with its "
                         f"{f['guest_feature'] or 'fitting face'}: {f['kind']}, {size_text(f)}. Build that face with this size.")
    if not lines:
        return ""
    return ("\nHow this part fits with the other parts on this sheet (from the assembly views). Each fit is a feature this part MUST have, "
            "with the shared size; a part that misses one cannot be assembled:\n" + "\n".join(lines))


def _tol(d: float) -> float:
    return max(0.2, 0.005 * d)


def _length(face: dict) -> float:
    return sum((face["to"][k] - face["from"][k]) ** 2 for k in range(3)) ** 0.5


def check_fits(fits: list[dict], parts: list[dict]) -> dict[str, list[str]]:
    """{part id: [what is wrong]}: each built part measured against the fits it takes part in, from its round faces (diameter, length along its
    axis). A part with no round faces read (SolidWorks absent) is not judged. Only round and groove fits can be measured this way."""
    faces = {p["id"]: p.get("round_faces") for p in parts}
    names = {p["id"]: p["name"] for p in parts}
    out: dict[str, list[str]] = {}

    def wrong(pid, text):
        out.setdefault(pid, []).append(text)

    for f in fits:
        d = f.get("diameter")
        if f["kind"] not in ("round", "groove") or not d:
            continue
        host, guest = faces.get(f["host"]), faces.get(f["guest"])
        hname, gname = names.get(f["host"], f["host"]), names.get(f["guest"], f["guest"])
        near = lambda fs: [c for c in fs if abs(c["diameter"] - d) <= _tol(d)]
        if host is not None:
            # a groove's bottom may sit below the ring's inside diameter (radial clearance: an engine's Ø76 groove under a Ø79 ring was
            # "corrected" away from the drawing), never above it
            seats = near(host) if f["kind"] == "round" else [c for c in host if 0.85 * d <= c["diameter"] <= d + _tol(d)]
            if not seats:
                have = sorted({c["diameter"] for c in host})[:12]
                # the whole fit in one message: a groove corrected for its diameter alone came back for its width (two corrections spent)
                wide = f", {f['width']:g} mm wide" if f["kind"] == "groove" and f.get("width") else ""
                size = f"diameter {d:g} mm" if f["kind"] == "round" else f"bottom diameter at most {d:g} mm"
                wrong(f["host"], f"{hname} has no round face of {size} for its {f['host_feature'] or 'seat'} that holds {gname} "
                                 f"({f['kind']} fit{wide}; its round faces measure {have} mm)")
            elif f["kind"] == "groove" and f.get("width") and max(_length(c) for c in seats) < f["width"] - 0.1:
                wrong(f["host"], f"{hname}'s groove for {gname} is {max(_length(c) for c in seats):.2f} mm wide; the fit needs "
                                 f"{f['width']:g} mm")
        if guest is not None:
            if f["kind"] == "round" and not near(guest):
                have = sorted({c["diameter"] for c in guest})[:12]
                wrong(f["guest"], f"{gname} has no round face of diameter {d:g} mm for its {f['guest_feature'] or 'fitting face'} that sits "
                                  f"in {hname} (its round faces measure {have} mm)")
            if f["kind"] == "groove" and f.get("width") and guest:
                thick = max(_length(c) for c in guest)
                if thick > f["width"] + 0.1:
                    wrong(f["guest"], f"{gname} is {thick:.2f} mm thick along its axis; it sits in {hname}'s groove of width "
                                      f"{f['width']:g} mm")
    return out


def correction(name: str, problems: list[str]) -> str:
    """The message the SolidWorks agent gets to correct one built part (it edits the saved part, it does not start again)."""
    return (f"Assembly check of {name}: it does not fit the parts it is assembled with. Correct the part so that each fit holds, using the "
            f"drawing's printed sizes:\n- " + "\n- ".join(problems)[:1800])


def pair_fits(fits: list[dict], a: str, b: str) -> list[dict]:
    """The fits between two parts, in either direction."""
    return [f for f in fits if {f["host"], f["guest"]} == {a, b}]
