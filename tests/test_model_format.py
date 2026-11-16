"""A part SolidWorks 2015+ saved (its own container, not OLE) is a native part: every part this app built on SolidWorks 2026 was refused."""
import tempfile
from pathlib import Path

import fakes  # noqa: F401
from app.assembly.assets import import_model


def test_old_and_new_solidworks_part_files_are_accepted_anything_else_is_not():
    with tempfile.TemporaryDirectory() as d:
        assert import_model("old.SLDPRT", bytes.fromhex("d0cf11e0a1b11ae1") + b"x", Path(d))["sha256"]
        assert import_model("Piston.SLDPRT", bytes.fromhex("c5a9208000000004") + b"x", Path(d))["sha256"]
        try:
            import_model("fake.SLDPRT", b"not a cad file", Path(d))
            raise AssertionError("a file that is not a SolidWorks part must be refused")
        except ValueError as ex:
            assert "SolidWorks" in str(ex)


def test_round_faces_give_each_cylinder_its_axis_segment_in_the_parts_own_frame():
    """The placing AI gets a part's bores / pins as axis segments (rods and caps landed inside an engine's crankshaft from bounding boxes)."""
    import math
    from app.assembly.from_parts import round_faces
    r = 0.02                                                                    # a Ø40 bore along z from z=0 to z=0.038, centred at x=0.1
    circle = lambda z: [{"start": [0.1 + r * math.cos(t), r * math.sin(t), z], "end": [0.1 + r * math.cos(t + 1), r * math.sin(t + 1), z],
                         "mid": [0.1 + r * math.cos(t + .5), r * math.sin(t + .5), z]} for t in (0, 2, 4)]
    faces = [{"kind": "cylinder", "origin": [0.1, 0, 0.5], "axis": [0, 0, -1], "radius": r},
             {"kind": "cylinder", "origin": [0, 0, 0], "axis": [0, 0, 1], "radius": 0.003}]             # an R3 rounding: left out
    got = round_faces(faces, circle(0.0) + circle(0.038) + [{"start": [9, 9, 9], "end": [9, 9, 8], "mid": [9, 9, 8.5]}])
    assert got == [{"diameter": 40.0, "axis": [0.0, 0.0, -1.0], "from": [100.0, 0.0, 38.0], "to": [100.0, 0.0, 0.0]}]


def test_a_part_that_sits_on_another_is_centred_on_its_round_face_exactly():
    """The AI picks WHICH faces meet (a rod's big-end bore on its crankpin); the app computes the origin (the AI's sums put rods inside a
    crankshaft). Axes that are not parallel in the chosen orientation keep the AI's origin and are reported."""
    from app.assembly.from_parts import DraftAssembly, snap
    built = [{"id": "crank", "round_faces": [{"diameter": 54, "axis": [0, 0, 1], "from": [0, 44, 100], "to": [0, 44, 146]}]},   # a crankpin
             {"id": "rod", "round_faces": [{"diameter": 54, "axis": [0, 0, 1], "from": [0, -111.8, -17], "to": [0, -111.8, 21]}]}]  # its big end
    draft = DraftAssembly.model_validate({"placements": [
        {"part": "crank", "copy": 1, "origin": [0, 0, 0], "x_to": "+X", "y_to": "+Y", "evidence": "anchor"},
        {"part": "rod", "copy": 1, "origin": [5, 5, 5], "x_to": "+X", "y_to": "+Y", "evidence": "guess",
         "on": {"part": "crank", "copy": 1, "my_face": 0, "its_face": 0}},
        {"part": "rod", "copy": 2, "origin": [7, 7, 7], "x_to": "+Z", "y_to": "+Y", "evidence": "turned",
         "on": {"part": "crank", "copy": 1, "my_face": 0, "its_face": 0}}]})
    origins, problems = snap(draft, built)
    assert origins[("rod", 1)] == [0.0, 155.8, 121.0]          # big-end centre (0, -111.8, 2) lands on the crankpin centre (0, 44, 123)
    assert origins[("rod", 2)] == [7, 7, 7] and "not parallel" in problems[0]


def test_two_crankpins_on_one_axis_line_are_two_cylinders_not_one():
    """Crankpins 1 and 4 of an inline four share one axis line: each face is cut at its own rims (the point on the face picks them)."""
    import math
    from app.assembly.from_parts import round_faces
    r = 0.027
    rim = lambda z: [{"start": [r * math.cos(t), 0.044 + r * math.sin(t), z], "end": [r * math.cos(t + 1), 0.044 + r * math.sin(t + 1), z],
                      "mid": [r * math.cos(t + .5), 0.044 + r * math.sin(t + .5), z]} for t in (0, 3)]
    edges = rim(0.1) + rim(0.146) + rim(0.4) + rim(0.446)
    pin = lambda z: {"kind": "cylinder", "origin": [0, 0.044, 0], "axis": [0, 0, 1], "radius": r, "point": [r, 0.044, z]}
    got = round_faces([pin(0.12), pin(0.42)], edges)
    assert sorted((c["from"][2], c["to"][2]) for c in got) == [(100.0, 146.0), (400.0, 446.0)]
