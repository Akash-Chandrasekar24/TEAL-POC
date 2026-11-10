"""Write the build lists for the pump and Wankel parts that had none (eval/build_lists/pump_*.json, wankel_*.json).

Numbers the sheet does not print are written as ASSUMED in `derived_from`, the item is `uncertain` and a question goes into `needs_human`:
the build waits until the designer confirms or corrects them on the Review page. Re-run after editing: python scripts/write_missing_part_lists.py
"""
import json
import math
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "eval" / "build_lists"


def item(i, feature, params, printed, derived_from="", uncertain=False, note=""):
    d = {"id": i, "feature": feature, "printed": printed, "params": params}
    if derived_from:
        d |= {"derived": True, "derived_from": derived_from}
    if uncertain:
        d["uncertain"] = True
    if note:
        d["note"] = note
    return d


def circle_outline(r):
    """A full circle as three 120-degree arcs (an outline needs at least three segments)."""
    pts = [(r * math.cos(math.radians(a)), r * math.sin(math.radians(a))) for a in (0, 120, 240)]
    segs = [{"kind": "arc", "cx": 0, "cy": 0, "x1": round(p[0], 9), "y1": round(p[1], 9), "x2": round(q[0], 9), "y2": round(q[1], 9), "dir": 1}
            for p, q in zip(pts, pts[1:] + pts[:1])]
    return {"shape": "segments", "frame": "centre", "segments": segs}


def write(name, part, seen, items, needs_human=(), unsupported=()):
    data = {"list_version": "1", "unit": "mm", "projection": "third", "part_name": part, "seen_dimensions": list(seen), "items": items,
            "needs_human": list(needs_human), "accepted_gaps": [],
            "unsupported": [{"id": f"U{n + 1}", "text": t} for n, t in enumerate(unsupported)]}
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ water pump
write("pump_casing", "Water pump 01 PUMP CASING (sheet 1, section A-A; sheet 3 section E-E)",
      ["230", "155", "75", "215", "132", "18", "Ø140 (IMPELLER CHAMBER)", "72", "134", "Ø50 (INLET)", "8x Ø10.5 (THRU) PCD Ø170", "t8"],
      [item("I1", "extrude_profile_boss", {"profile": circle_outline(105), "depth": 134},
            "230 wide (155 + 75), 134 deep (section A-A)",
            "body Ø210: the left edge is 230 - 155 - 75 + 105 ... read as 105 from the inlet axis (feet 25 + 160 + 25 = 210 centred on it); "
            "ASSUMED round body: the volute outline is not dimensioned", uncertain=True),
       item("I2", "cut", {"shapes": [{"kind": "circle", "position": {"x": 0, "y": 0}, "diameter": 140}], "face": "back", "depth": 72},
            "Ø140 (IMPELLER CHAMBER), 72 wide", "ASSUMED open to the back (motor side), as drawn in section A-A", uncertain=True),
       item("I3", "hole", {"positions": [{"x": 0, "y": 0}], "diameter": 50, "face": "front", "depth": "through", "material_length": 62},
            "Ø50 (INLET)", "material crossed = 134 - 72 = 62 (the front wall up to the chamber)"),
       item("I4", "swept_passage", {"operation": "cut", "diameter": 16, "end_diameter": 28, "plane_z": 36,
                                    "path": {"kind": "spiral", "centre": {"x": 0, "y": 0}, "start_radius": 75, "end_radius": 85, "sweep_deg": 270,
                                             "start_angle_deg": 0, "hand": "counter_clockwise"}},
            "VOLUTE PASSAGE (sheet 3 section E-E, not dimensioned)",
            "ASSUMED: cutwater at r 75 (just outside the Ø140 chamber), throat at r 85, section Ø16 widening to Ø28, 270 degrees round, "
            "in the middle of the chamber (72 / 2 = 36 from the back)", uncertain=True),
       item("I5", "hole", {"pattern": {"kind": "circular", "centre": {"x": 0, "y": 0}, "pitch_diameter": 170, "count": 8, "start_angle_deg": 0},
                           "diameter": 10.5, "face": "back", "depth": 18},
            "8x Ø10.5 (THRU) PCD Ø170", "ASSUMED the holes go through an 18 thick motor-side flange (18 as the feet); first hole on +x", uncertain=True)],
      ["The volute is not dimensioned: give its start / end radius, section size at the cutwater and at the throat, and how far round it winds "
       "(assumed r 75 -> 85, Ø16 -> Ø28, 270 degrees).",
       "The casing outline is not dimensioned: is a Ø210 round body acceptable for the model, or give the volute's outer outline?",
       "8x Ø10.5 PCD Ø170: how thick is the flange they pass through, and where is the first hole? (assumed 18, on +x)"],
      ["Discharge neck: Ø70 neck with the Ø40 passage rising 100 above the axis, Ø110 x 12 flange (not on the menu: a neck at the side of the volute).",
       "Feet: 18 high, 25 / 160 / 25, cast with the body.",
       "Cast walls t8 are modelled as solid material outside the chamber and volute."])

write("pump_motor_housing", "Water pump 02 MOTOR HOUSING (sheet 1, side view and section B-B)",
      ["248", "40", "168", "Ø150", "Ø140", "Ø120 STATOR CAVITY", "t8", "Ø35 (BRG) SEAT", "Ø25 (SHAFT BORE)", "210"],
      [item("I1", "revolve_base", {"axis": "horizontal", "profile": {"shape": "polygon", "points": [
          [0, 17.5], [0, 75], [40, 75], [40, 70], [208, 70], [208, 75], [248, 75], [248, 17.5], [240, 17.5], [240, 60], [8, 60], [8, 17.5]]}},
            "Ø150 end flanges 40 long, Ø140 body 168 long, Ø120 stator cavity, end walls t8, Ø35 bearing seats",
            "radii are half the printed diameters (75, 70, 60, 17.5); 40 + 168 = 208, + 40 = 248; cavity from 8 to 240 (t8 walls); "
            "the Ø35 seats go through the 8 thick end walls", uncertain=True),
       item("I2", "radial_blades", {"count": 24, "inner_diameter": 140, "outer_diameter": 150, "thickness": 4, "from_u": 40, "to_u": 208, "shape": "straight"},
            "cooling fins on the Ø140 body (drawn, not dimensioned)",
            "ASSUMED 24 fins, 4 thick, standing up to the Ø150 of the end flanges, over the 168 body", uncertain=True)],
      ["Overall length: the side view prints 248 (40 + 168 + 40) but section B-B prints 210 under the same chain. Which is right? (248 used)",
       "Cooling fins are not dimensioned: how many, how thick, how tall? (assumed 24 x 4 thick, up to Ø150)",
       "Bearing seats Ø35: how deep are they, and where does the Ø25 shaft bore run? (modelled: Ø35 straight through the t8 end walls)"],
      ["Mounting feet (15 high, 4x Ø12 THRU on 140 x 40, 35 / 140 / 35) are not on a turned part's menu.",
       "Eye-bolt boss on top (the eye bolt itself is a bought part)."])

write("pump_impeller", "Water pump 01 IMPELLER (sheet 2, front view and section C-C)",
      ["Ø140", "Ø110", "Ø70", "Ø50 (EYE)", "36", "6", "t = 6.0", "HUB (t = 16.0)", "18", "≈22"],
      [item("I1", "revolve_base", {"axis": "horizontal", "profile": {"shape": "polygon", "points": [
          [0, 25], [0, 70], [6, 70], [6, 35], [30, 35], [30, 70], [36, 70], [36, 25]]}},
            "front shroud Ø140 t = 6.0, rear shroud Ø140 t = 6.0, hub Ø70 with a Ø50 bore, 36 overall",
            "front shroud 0 to 6, rear shroud 36 - 6 = 30 to 36, joined by the Ø70 hub (the eye is opened by I3); bore Ø50 = the shaft's impeller seat",
            uncertain=True, note="HUB (t = 16.0) is not reproduced: the hub here runs the full 36 to join both shrouds."),
       item("I2", "radial_blades", {"count": 7, "inner_diameter": 70, "outer_diameter": 140, "thickness": 4, "from_u": 6, "to_u": 30, "shape": "arc",
                                    "inlet_angle_deg": 25, "outlet_angle_deg": 30, "hand": "clockwise"},
            "7 curved blades (drawn), blade passage between the shrouds (6 to 30)",
            "between the front shroud (0 to 6) and the rear shroud (30); ASSUMED 4 thick, beta 1 = 25, beta 2 = 30 degrees, leaning as drawn", uncertain=True),
       item("I3", "turned_axial", {"operation": "cut", "diameter": 110, "from_u": 0, "to_u": 6}, "Ø110 opening of the front shroud (the eye)",
            "the Ø110 read as the front shroud's eye opening; it also opens the hub inside it", uncertain=True)],
      ["Blade count, thickness and the inlet / outlet blade angles are not printed (assumed 7 blades, 4 thick, beta 1 = 25, beta 2 = 30).",
       "Section C-C: is Ø110 the front shroud's opening (the front view shows a Ø50 eye)? The blade passage is printed ≈22 but 36 - 6 - 6 = 24."],
      [])

write("pump_cooling_fan", "Water pump 08 COOLING FAN (sheet 2)",
      ["Ø160", "Ø60", "Ø28 H7", "32"],
      [item("I1", "revolve_base", {"axis": "horizontal", "profile": {"shape": "polygon", "points": [[0, 14], [0, 30], [32, 30], [32, 14]]}},
            "hub Ø60 x 32 with a Ø28 H7 bore", "radii are half the printed diameters (30, 14)", note="H7 is a fit note; the nominal Ø28 bore is modelled."),
       item("I2", "radial_blades", {"count": 12, "inner_diameter": 60, "outer_diameter": 160, "thickness": 3, "from_u": 0, "to_u": 32, "shape": "straight"},
            "12 blades (drawn) from the Ø60 hub to Ø160", "ASSUMED 3 thick, full hub width", uncertain=True)],
      ["Fan blades: how thick are they, and do they run the full 32 width (the side view shows them tapering)? (assumed 3 thick, full width, 12 blades as drawn)"],
      ["The blades' taper toward the tip (side view) is not modelled."])

write("pump_bearing_housing", "Water pump 03 BEARING HOUSING / END COVER (sheet 2)",
      ["110", "88", "11", "Ø106", "Ø72", "Ø62", "Ø35 (BRG SEAT)", "18", "40", "76", "4x Ø10.5 (THRU) PCD Ø90"],
      [item("I1", "revolve_base", {"axis": "horizontal", "profile": {"shape": "polygon", "points": [
          [0, 36], [0, 53], [76, 53], [76, 17.5], [58, 17.5], [58, 31], [18, 31], [18, 36]]}},
            "Ø106 body, bores Ø72 x 18, Ø62 x 40 (bearing), Ø35 x 18, 76 long",
            "radii are half the printed diameters (53, 36, 31, 17.5); 18 + 40 = 58, + 18 = 76"),
       *[item(f"I{n + 2}", "turned_axial", {"operation": "cut", "diameter": 10.5, "from_u": 0, "to_u": 76, **off},
              "4x Ø10.5 (THRU) PCD Ø90", "PCD Ø90 -> 45 from the axis, through the 76 length", uncertain=True)
         for n, off in enumerate(({"offset_in_view": 45}, {"offset_in_view": -45}, {"offset_toward_viewer": 45}, {"offset_toward_viewer": -45}))]],
      ["Bolt holes: the note says PCD Ø90, but they are drawn 88 apart on the corners of a 110 x 106 flange (a PCD of about 124, outside the Ø106 body). "
       "Which is right? (PCD Ø90 through the body used)"],
      ["Square flange with four lugs (110 x 106) is not on a turned part's menu; the body is modelled round (Ø106)."])

for name, part in (("pump_bearing_de", "Water pump 05 BEARING (DE), 6306 (sheet 2 detail 04)"), ("pump_bearing_nde", "Water pump 08 BEARING (NDE), 6306 (sheet 2 detail 04)")):
    write(name, part, ["Ø30", "Ø62", "16", "6306 (Ø30×Ø62×16)"],
          [item("I1", "revolve_base", {"axis": "horizontal", "profile": {"shape": "polygon", "points": [
              [0, 15], [0, 20], [2, 20], [2, 27], [0, 27], [0, 31], [16, 31], [16, 27], [14, 27], [14, 20], [16, 20], [16, 15]]}},
                "6306 (Ø30×Ø62×16): bore Ø30, outside Ø62, 16 wide",
                "radii 15 and 31 = Ø30 / 2 and Ø62 / 2; ASSUMED the inner ring to r 20 and the outer ring from r 27, with the 2 deep "
                "recess between them on both faces where the balls and cage sit (the rings and the shields as one body)", uncertain=True)],
          ["The sheet prints 6306 as Ø30 x Ø62 x 16, which are the sizes of a 6206; a real 6306 is Ø30 x Ø72 x 19. Which sizes should the model use? (Ø30 x Ø62 x 16 used)",
           "Ring sizes are not printed: inner ring up to Ø40, outer ring from Ø54, recess 2 deep on both faces. Confirm or give the sizes."],
          ["The balls and cage are not modelled one by one: the bearing is one solid with the rings and a recess between them."])

write("pump_mechanical_seal", "Water pump 06 MECHANICAL SEAL (sheet 2 detail 05)", ["Ø35 (SHAFT)", "Ø42", "Ø25", "12", "18", "6", "36"],
      [item("I1", "revolve_base", {"axis": "horizontal", "profile": {"shape": "polygon", "points": [
          [0, 17.5], [0, 21], [12, 21], [12, 19.5], [30, 19.5], [30, 21], [36, 21], [36, 17.5]]}},
            "rotating face 12, spring 18, seat 6 (36 overall) on the Ø35 shaft, Ø42 outside",
            "bore 17.5 = Ø35 / 2, outside 21 = Ø42 / 2; 12 + 18 = 30, + 6 = 36; ASSUMED the spring zone at Ø39 (r 19.5) between the two faces", uncertain=True)],
      ["The seal's sizes are only partly printed (detail 05 is an assembly): is Ø42 the outside of both faces, what is the spring's outside diameter, "
       "and what does the Ø25 refer to? (modelled: Ø42 faces, Ø39 spring zone, Ø35 bore)"],
      ["The spring, the carbon face and the SiC face are modelled as one stepped ring."])

# ------------------------------------------------------------------ wankel
write("wankel_rotor_housing", "Wankel 01 ROTOR HOUSING (sheet 1)",
      ["220", "30", "160", "205", "37.5", "130", "68", "R65.000 (TROCHOID)", "e = 15.00", "12x Ø10.5 (THRU) PCD Ø190", "30° (TYP.)", "Ø16 COOLANT", "48", "18"],
      [item("I1", "extrude_profile_boss", {"profile": {"shape": "rectangle", "width": 220, "height": 205}, "depth": 68},
            "220 x 205 outline, 68 wide (section A-A)", "the lobed outer outline is not dimensioned: modelled as its 220 x 205 envelope", uncertain=True),
       item("I2", "profile_cut", {"profile": {"shape": "epitrochoid", "generating_radius": 65, "eccentricity": 15, "rotation_deg": 90},
                                  "face": "front", "depth": "through", "material_length": 68},
            "R65.000 (TROCHOID), e = 15.00 (sheet 2 eccentric shaft)", "major axis drawn vertical (rotation 90)", uncertain=True),
       item("I3", "hole", {"pattern": {"kind": "circular", "centre": {"x": 0, "y": 0}, "pitch_diameter": 190, "count": 12, "start_angle_deg": 90},
                           "diameter": 10.5, "face": "front", "depth": "through", "material_length": 68},
            "12x Ø10.5 (THRU) PCD Ø190, 30° (TYP.)", "12 at 30 degrees, one on top as in detail E"),
       *[item(f"I{4 + n}", "hole", {"positions": [{"x": 0, "y": 48, "y_from": "bottom_edge"}], "diameter": 16, "face": face, "depth": 18},
              f"Ø16 COOLANT {'IN' if face == 'left' else 'OUT'}, 48 from the bottom, 18 deep (detail 08)",
              "centred in the 68 width", uncertain=True) for n, face in enumerate(("left", "right"))]],
      ["The trochoid: R65 with e = 15 gives a bore 160 x 100, but the front view prints 130 between the bolt rows and R41.200 as a base circle. "
       "Confirm R and e (and that the long axis is vertical).",
       "Coolant ports: are they centred in the 68 width, and how far does the Ø12 continue past the Ø16 x 18 counterbore?"],
      ["Lobed outer outline round the bolt bosses (not dimensioned).",
       "Water jacket (peripheral cooling, section A-A) is not dimensioned.",
       "Spark plug boss (detail 05: Ø24, 36 / 22 long, M14x1.25, 32 x 28 pad R8) sits on the curved trochoid wall: not on the menu (angled_boss stands on the front face only).",
       "Ø12 continuation of the coolant ports and the M14x1.25 thread."])

write("wankel_side_housing", "Wankel 02 SIDE HOUSING, intake side (sheet 1)",
      ["198", "205", "102.5", "38", "Ø45 H7", "10x Ø10.5 (THRU) PCD Ø170", "INTAKE PORT 64 x 22 R6 (4x)", "EXHAUST PORT 50 x 28 R8 (4x)", "57", "26", "146"],
      [item("I1", "extrude_profile_boss", {"profile": {"shape": "rectangle", "width": 198, "height": 205}, "depth": 38},
            "198 x 205 outline, 38 thick (section B-B)", "the lobed outline is not dimensioned: modelled as its 198 x 205 envelope", uncertain=True),
       item("I2", "hole", {"positions": [{"x": 0, "y": 0}], "diameter": 45, "face": "front", "depth": "through", "material_length": 38},
            "Ø45 H7, 102.5 / 102.5", "centred: 205 = 102.5 + 102.5", note="H7 is a fit note; the nominal Ø45 is modelled."),
       item("I3", "hole", {"pattern": {"kind": "circular", "centre": {"x": 0, "y": 0}, "pitch_diameter": 170, "count": 10, "start_angle_deg": 0},
                           "diameter": 10.5, "face": "front", "depth": "through", "material_length": 38},
            "10x Ø10.5 (THRU) PCD Ø170", "ASSUMED first hole on +x (10 at 36 degrees)", uncertain=True),
       item("I4", "profile_cut", {"profile": {"shape": "rectangle", "width": 22, "height": 64, "corner_radius": 6, "position": {"x": -50, "y": 25}},
                                  "face": "front", "depth": "through", "material_length": 38},
            "INTAKE PORT 22 x 64, R6 (4x)", "ASSUMED position (-50, 25) from the centre: not dimensioned; modelled straight through", uncertain=True),
       item("I5", "profile_cut", {"profile": {"shape": "rectangle", "width": 28, "height": 50, "corner_radius": 8, "position": {"x": 45, "y": -45.5}},
                                  "face": "front", "depth": "through", "material_length": 38},
            "EXHAUST PORT 28 x 50, R8 (4x), 57 from the bottom",
            "y = -102.5 + 57 = -45.5 (57 read to the port centre); ASSUMED x = 45; modelled straight through", uncertain=True)],
      ["Port positions are not fully dimensioned: where are the intake (22 x 64) and exhaust (28 x 50) port centres? (assumed (-50, 25) and (45, -45.5))",
       "Section B-B shows the ports turning out of the top / bottom edge, not straight through: confirm modelling them through the plate.",
       "10x Ø10.5 PCD Ø170: where is the first hole? (assumed on +x)"],
      ["Lobed outer outline (not dimensioned).", "Ports turning to the edge (section B-B, 18 deep from the top) are modelled straight through."])

write("wankel_rotor", "Wankel 03 ROTOR (sheet 2)",
      ["R65", "e = 15.00", "42.00", "Ø36.00 H7", "120.00", "90.00", "103.80"],
      [item("I1", "extrude_profile_boss", {"profile": {"shape": "rotor_flank", "generating_radius": 65, "eccentricity": 15}, "depth": 42},
            "rotor running in the R65 / e = 15 trochoid, 42.00 wide", uncertain=True),
       item("I2", "hole", {"positions": [{"x": 0, "y": 0}], "diameter": 36, "face": "front", "depth": "through", "material_length": 42},
            "Ø36.00 H7", note="H7 is a fit note; the nominal Ø36 is modelled.")],
      ["The rotor sheet is inconsistent: R65 / e = 15 give a rotor 112.58 apex to apex, but the sheet prints 120.00 (and 90.00 high vs about 97). "
       "Which is right: the trochoid numbers or the printed rotor size?"],
      ["Oil pockets (3x 120 degrees, R6.0): not dimensioned.", "Apex, side and corner seal grooves (details 02 and 07).",
       "Internal gear seat (the ring gear is a separate part)."])

for name, part, w, h, d, chamfer in (("wankel_apex_seal", "Wankel 08 APEX SEAL (sheet 2)", 38, 3, 3, True),
                                     ("wankel_side_seal", "Wankel 09 SIDE SEAL (sheet 2)", 32, 2.5, 2.5, False),
                                     ("wankel_corner_seal", "Wankel 10 CORNER SEAL (sheet 2)", 12, 2, 3, True)):
    write(name, part, [f"{w:.2f}", f"{h:.2f}", f"{d:.2f}"] + (["30°"] if chamfer else []),
          [item("I1", "extrude_profile_boss", {"profile": {"shape": "rectangle", "width": w, "height": h}, "depth": d},
                f"{w:.2f} x {h:.2f}, {d:.2f}")],
          [], ["The 30° tip chamfer is not modelled (a chamfer with two different setbacks)."] if chamfer else [])

write("wankel_dowel", "Wankel 11 DOWEL (sheet 1 detail 06)", ["Ø8 H7 (DOWEL)", "12"],
      [item("I1", "revolve_base", {"axis": "horizontal", "profile": {"shape": "polygon", "points": [[0, 0], [24, 0], [24, 4], [0, 4]]}},
            "Ø8 dowel; the hole is 12 deep (detail 06)", "radius 4 = Ø8 / 2; ASSUMED length 24 = 12 into each housing", uncertain=True)],
      ["The dowel's length is not printed: 24 (12 into each of the two housings)?"], [])
print("written")
