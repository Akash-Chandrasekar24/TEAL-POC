"""Parts that assemble on their own: the fits read from the drawing, the built parts measured against them, round faces whose ends are partial
arcs, a pin placed at a web's box centre, copies of one part in one place, and the repair loop around the SolidWorks assembly build."""
import math

import fakes  # noqa: F401
from app.assembly.fits import check_fits, correction, fits_text, pair_fits, parse_fits
from app.assembly.from_parts import copy_overlaps, round_faces
from app.assembly.model import AssemblyPlan
from app.menu.buildlist import BuildList, off_lobe_bosses

PIN = {"host": "piston", "host_feature": "pin bore", "guest": "pin", "guest_feature": "outside", "kind": "round", "diameter": 33.8,
       "width": None, "evidence": "section A-A, Ø33.8"}
RING = {"host": "piston", "host_feature": "top ring groove", "guest": "ring", "guest_feature": "section", "kind": "groove", "diameter": 76,
        "width": 2.0, "evidence": "detail B"}
ROOM = {"host": "piston", "host_feature": "inside between the pin bosses", "guest": "rod", "guest_feature": "small end", "kind": "cavity",
        "diameter": None, "width": 38, "evidence": "rod width 38"}


def cyl(d, z0, z1, axis=(0, 0, 1)):
    return {"diameter": d, "axis": list(axis), "from": [0, 0, z0], "to": [0, 0, z1]}


def test_fits_keep_only_two_different_known_parts_and_a_known_kind():
    data = {"fits": [PIN, {**PIN, "guest": "piston"}, {**PIN, "host": "bolt"}, {**PIN, "kind": "glued"}, {**PIN, "diameter": -1}, ROOM]}
    fits = parse_fits(data, ["piston", "pin", "rod"])
    assert [f["kind"] for f in fits] == ["round", "cavity"]
    assert parse_fits("not json", ["piston"]) == [] and parse_fits({"fits": "x"}, ["piston"]) == []


def test_each_part_is_told_its_side_of_every_fit():
    names = {"piston": "Piston", "pin": "Piston Pin", "rod": "Connecting Rod"}
    host = fits_text([PIN, ROOM], "piston", names)
    assert "HOLDS Piston Pin" in host and "diameter 33.8 mm" in host and "HOLDS Connecting Rod" in host and "must be empty" in host
    guest = fits_text([PIN, ROOM], "pin", names)
    assert "SITS in Piston" in guest and "Connecting Rod" not in guest
    assert fits_text([PIN], "crank", names) == ""


def test_a_part_without_the_shared_diameter_or_with_a_narrow_groove_is_named():
    parts = [{"id": "piston", "name": "Piston", "round_faces": [cyl(85, 0, 90), cyl(76, 10, 12)]},       # no Ø33.8 bore; groove 2 mm wide
             {"id": "pin", "name": "Piston Pin", "round_faces": [cyl(33.8, 0, 70)]},
             {"id": "ring", "name": "Piston Ring", "round_faces": [cyl(85, 0, 4.3), cyl(79, 0, 4.3)]}]    # 4.3 thick in a 2 mm groove
    bad = check_fits([PIN, RING, ROOM], parts)
    assert set(bad) == {"piston", "ring"}
    assert "no round face of diameter 33.8" in bad["piston"][0]
    assert "4.30 mm thick" in bad["ring"][0]
    msg = correction("Piston", bad["piston"])
    assert msg.startswith("Assembly check of Piston") and "33.8" in msg
    good = [{"id": "piston", "name": "Piston", "round_faces": [cyl(85, 0, 90), cyl(33.85, -40, 40), cyl(76, 10, 14.5)]},
            {"id": "pin", "name": "Piston Pin", "round_faces": [cyl(33.8, 0, 70)]},
            {"id": "ring", "name": "Piston Ring", "round_faces": [cyl(85, 0, 2), cyl(79, 0, 2)]}]
    assert check_fits([PIN, {**RING, "width": 4.3}, ROOM], good) == {}
    assert check_fits([PIN], [{"id": "piston", "name": "Piston"}, {"id": "pin", "name": "Pin"}]) == {}    # nothing read: not judged
    # the ring's inside is Ø79; the drawing's groove bottom is Ø76: clearance behind the ring, not a misfit (it was "corrected" to Ø79)
    behind = [{"id": "piston", "name": "Piston", "round_faces": [cyl(85, 0, 90), cyl(76, 10, 14.3)]},
              {"id": "ring", "name": "Piston Ring", "round_faces": [cyl(85, 0, 4.3), cyl(79, 0, 4.3)]}]
    assert check_fits([{**RING, "diameter": 79, "width": 4.3}], behind) == {}
    too_big = [{"id": "piston", "name": "Piston", "round_faces": [cyl(85, 0, 90), cyl(82, 10, 14.3)]}]
    assert "bottom diameter at most 79" in check_fits([{**RING, "diameter": 79, "width": 4.3}], too_big)["piston"][0]
    assert pair_fits([PIN, ROOM], "rod", "piston") == [ROOM]


def _face(r, origin, point, length):
    return {"kind": "cylinder", "radius": r, "origin": origin, "axis": [0, 0, 1], "point": point, "area": 2 * math.pi * r * length}


def _circle(r, cx, cy, z):
    return {"start": [cx + r, cy, z], "mid": [cx - r, cy, z], "end": [cx + r, cy, z]}


def test_round_faces_whose_ends_are_partial_arcs_take_their_length_from_their_area():
    # a pin off its webs' centre: no edge lies wholly on it (both ends are partial arcs); journals on one axis line: only the two shaft ends
    # are full circles, so each journal spanned the whole shaft. Metres, as SolidWorks gives them.
    pin = _face(0.027, [0, 0.044, 0.129], [0.027, 0.044, 0.152], 0.046)
    j1 = _face(0.036, [0, 0, -0.038], [0.036, 0, -0.019], 0.038)
    j2 = _face(0.036, [0, 0, 0.076], [0.036, 0, 0.095], 0.038)
    edges = [_circle(0.036, 0, 0, -0.038), _circle(0.036, 0, 0, 0.453)]
    faces = {(c["diameter"], c["from"][2], c["to"][2]) for c in round_faces([pin, j1, j2], edges)}
    assert (54.0, 129.0, 175.0) in faces
    assert (72.0, -38.0, 0.0) in faces and (72.0, 76.0, 114.0) in faces
    assert not any(d == 72.0 and z1 - z0 > 40 for d, z0, z1 in faces)


def test_a_full_cylinder_with_its_own_rims_keeps_the_rims():
    bore = _face(0.0169, [0, 0, 0], [0.0169, 0, 0.035], 0.07)
    got = round_faces([bore], [_circle(0.0169, 0, 0, 0.0), _circle(0.0169, 0, 0, 0.07)])
    assert [(c["diameter"], c["from"][2], c["to"][2]) for c in got] == [(33.8, 0.0, 70.0)]


def _crank_list(pin_params):
    web = {"depth": 15, "face": "front", "offset": 99, "profile": {"frame": "base_lower_left", "segments": [
        {"kind": "arc", "x1": 72, "y1": 124, "x2": 0, "y2": 124, "cx": 36, "cy": 124, "dir": 1},
        {"kind": "line", "x1": 0, "y1": 124, "x2": 0, "y2": 80},
        {"kind": "arc", "x1": 0, "y1": 80, "x2": 72, "y2": 80, "cx": 36, "cy": 80, "dir": 1},
        {"kind": "line", "x1": 72, "y1": 80, "x2": 72, "y2": 124}]}}
    return BuildList.model_validate({"list_version": "1", "part_name": "crank", "unit": "mm", "projection": "first", "seen_dimensions": [],
                                     "items": [{"id": "F1", "feature": "extrude_profile_boss", "printed": "", "params": {"depth": 15, "profile": web["profile"]}},
                                               {"id": "F7", "feature": "extrude_profile_boss", "printed": "", "params": web},
                                               {"id": "F8", "feature": "circle_boss", "printed": "", "params": pin_params}]})


def test_a_pin_at_the_centre_of_a_web_outline_box_is_refused_with_where_its_arc_is():
    at_box_centre = {"circles": [{"diameter": 54, "position": {"x": 0, "y": 0}}], "height": 46, "on": "F7.top"}
    reasons = off_lobe_bosses(_crank_list(at_box_centre))
    assert len(reasons) == 1 and reasons[0].startswith("F8:") and "(36, 124) R36" in reasons[0] and "BOUNDING BOX" in reasons[0]
    by_coordinates = {"circles": [{"diameter": 54, "position": {"x": 36, "x_from": "left_edge", "y": 124, "y_from": "bottom_edge"}}],
                      "face": "front", "height": 46, "offset": 114}
    assert off_lobe_bosses(_crank_list(by_coordinates)) == []
    offset_given = {"circles": [{"diameter": 54, "position": {"x": 0, "y": 22}}], "height": 46, "on": "F7.top"}
    assert off_lobe_bosses(_crank_list(offset_given)) == []
    too_big_for_the_arcs = {"circles": [{"diameter": 90, "position": {"x": 0, "y": 0}}], "height": 5, "on": "F7.top"}
    assert off_lobe_bosses(_crank_list(too_big_for_the_arcs)) == []


def _plan(origins):
    row = {"id": "item-piston", "part_number": "Piston", "description": "Piston", "quantity": len(origins), "kind": "part",
           "evidence": [{"page": 1, "text": "Piston x4"}]}
    return AssemblyPlan.model_validate({"name": "e", "source_hash": "h", "unit": "mm", "bom": [row],
                                        "models": [{"bom_id": "item-piston", "sha256": "a" * 64, "source_name": "Piston.SLDPRT",
                                                    "configuration": "Default", "provenance": "TEST"}],
                                        "occurrences": [{"id": f"item-piston-{n}", "bom_id": "item-piston", "fixed": True,
                                                         "placement": {"translation": o, "rotation": [1, 0, 0, 0, 1, 0, 0, 0, 1], "evidence": "TEST"}}
                                                        for n, o in enumerate(origins, 1)]})


def test_copies_of_one_part_in_one_place_are_caught_before_solidworks():
    built = [{"id": "piston", "bbox_mm": [[-42.5, -42.5, 0], [42.5, 42.5, 90]]}]
    crowded = copy_overlaps(_plan([[0, 0, 0], [60, 0, 0], [228, 0, 0]]), built)
    assert [(c["a"], c["b"]) for c in crowded] == [("item-piston-1", "item-piston-2")] and crowded[0]["mm3"] == 25 * 85 * 90
    assert copy_overlaps(_plan([[0, 0, 0], [114, 0, 0], [228, 0, 0]]), built) == []
    assert copy_overlaps(_plan([[0, 0, 0], [85, 0, 0]]), built) == []                # touching boxes are fine


def test_the_assembly_repairs_parts_and_placements_on_its_own():
    import app.web.server as s
    batch = {"items": [{"part_id": p, "name": p.title(), "job_id": f"job-{p}"} for p in ("piston", "pin", "rod")], "assembly": {}}
    calls = {"assemble": 0, "build": 0, "fix": []}
    fits = [PIN, ROOM]
    plan = _plan([[0, 0, 0]]).model_dump()

    def assemble_parts(rid, llm=None):
        calls["assemble"] += 1
        # first pass: the pin is too thin (measured); after its correction, the parts fit
        problems = {"pin": ["Piston Pin has no round face of diameter 33.8 mm"]} if calls["assemble"] == 1 else {}
        return {"plan": plan, "extracted": {}, "fit_problems": problems}

    def build(rid, attempt):
        calls["build"] += 1
        # build 1: the rod sits inside the solid piston (a cavity fit: the piston is corrected); build 2: clean
        batch["assembly"] = {**batch["assembly"], "status": "needs_human" if calls["build"] == 1 else "awaiting_review", "job_id": "asm"}

    saved = dict(s.__dict__)
    try:
        s.ensure_fits = lambda *a, **k: None
        s.load_fits = lambda rid: fits
        s.result_with_design = lambda rid: {}
        import app.web.parts as parts
        old_detect = parts.detect_parts
        parts.detect_parts = lambda result: [{"id": p, "name": p.title()} for p in ("piston", "pin", "rod")]
        s.load_batch = lambda rid: batch
        s._save_batch = lambda rid, b: None
        s.assemble_parts = assemble_parts
        s._build_assembly = build
        s.save_assembly = lambda rid, plan, *a: {"id": "att", "blockers": []}
        s.write_json = lambda *a, **k: None
        s._last_overlaps = lambda b: {"overlaps": [{"a": "item-piston-1", "b": "item-piston-1", "mm3": 5},
                                                   {"a": "item-piston-1", "b": "item-rod-1", "mm3": 113000}], "previous": []}
        plan["occurrences"].append({**plan["occurrences"][0], "id": "item-rod-1", "bom_id": "item-rod"})
        s._correct_part = lambda rid, pid, msg, llm=None: calls["fix"].append((pid, msg)) or f"{pid}: corrected"
        s._assemble_with_repairs("rid")
    finally:
        for k in ("ensure_fits", "load_fits", "result_with_design", "load_batch", "_save_batch", "assemble_parts", "_build_assembly",
                  "save_assembly", "write_json", "_last_overlaps", "_correct_part"):
            setattr(s, k, saved[k])
        parts.detect_parts = old_detect
    assert [p for p, _ in calls["fix"]] == ["pin", "piston"]
    assert "33.8" in calls["fix"][0][1] and "Rod overlaps it by 113.0 cm3 at the cavity fit" in calls["fix"][1][1]
    assert calls["build"] == 2 and batch["assembly"]["status"] == "awaiting_review"
    assert any("assembly build 1" in line for line in batch["assembly"]["repairs"])


def test_a_lock_left_by_a_stopped_server_does_not_block_the_job():
    import tempfile
    from pathlib import Path
    from app.web.workflow import BOOT, Workflow
    with tempfile.TemporaryDirectory() as tmp:
        wf = Workflow(tmp, plan=None)
        folder = wf.folder("abc123def456")
        folder.mkdir(parents=True)
        wf.save({"id": "abc123def456", "revision": 3, "phase": "awaiting_review"})
        (folder / "request.lock").write_text("0" * 32)                         # an earlier server process
        with wf.locked("abc123def456", 3) as record:
            assert record["phase"] == "awaiting_review"
        assert not (folder / "request.lock").exists()
        (folder / "request.lock").write_text(BOOT)                              # this process: still in progress
        try:
            with wf.locked("abc123def456", 3):
                raise AssertionError("a live lock must block")
        except ValueError as ex:
            assert "operation in progress" in str(ex)
        assert Path(folder / "request.lock").read_text() == BOOT


def test_an_assembly_built_without_some_parts_is_incomplete_not_done():
    import app.web.server as s
    import app.web.parts as parts
    batch = {"items": [], "assembly": {}}
    plan = _plan([[0, 0, 0]]).model_dump()

    def build(rid, attempt):
        batch["assembly"] = {**batch["assembly"], "status": "awaiting_review", "job_id": "asm"}

    names = ("ensure_fits", "load_fits", "result_with_design", "load_batch", "_save_batch", "assemble_parts", "_build_assembly",
             "save_assembly", "write_json")
    saved = {k: getattr(s, k) for k in names}
    old_detect = parts.detect_parts
    try:
        s.ensure_fits = lambda *a, **k: None
        s.load_fits = lambda rid: []
        s.result_with_design = lambda rid: {}
        parts.detect_parts = lambda result: [{"id": "piston", "name": "Piston"}]
        s.load_batch = lambda rid: batch
        s._save_batch = lambda rid, b: None
        s.assemble_parts = lambda rid, llm=None: {"plan": plan, "extracted": {}, "fit_problems": {}, "left_out": ["Connecting Rod: not built"]}
        s._build_assembly = build
        s.save_assembly = lambda rid, plan, *a: {"id": "att", "blockers": []}
        s.write_json = lambda *a, **k: None
        s._assemble_with_repairs("rid")
    finally:
        for k, v in saved.items():
            setattr(s, k, v)
        parts.detect_parts = old_detect
    assert batch["assembly"]["status"] == "incomplete" and "Connecting Rod" in batch["assembly"]["detail"]


def test_the_best_version_of_a_part_is_assembled_not_the_newest():
    from app.assembly.versions import attributed, choose, scores
    fits = [PIN, ROOM, {**PIN, "host": "rod", "guest": "crank", "diameter": 54}]
    # build 1: first rod (r1) touches the crankpin fillets a little; the solid piston (p1) holds the rod: the piston's fault (cavity)
    b1 = attributed([("r1", "rod", "c1", "crank", 122.5), ("r1", "rod", "p1", "piston", 67000.0)], fits)
    assert b1 == {"r1": 122.5, "c1": 122.5, "p1": 67000.0}
    # build 2: the rebuilt rod (r2) lost its big end: 102 cm3 into the crankshaft
    b2 = attributed([("r2", "rod", "c1", "crank", 102640.0)], fits)
    known = scores([b1, b2], [{"r1", "c1", "p1"}, {"r2", "c1", "p2"}])
    assert known == {"r1": 122.5, "c1": 122.5, "p1": 67000.0, "r2": 102640.0, "p2": 0.0}
    rods = [{"sha256": "r1", "mtime": 1}, {"sha256": "r2", "mtime": 2}]
    assert choose(rods, {}, known, current="r2")["sha256"] == "r1"
    assert choose(rods, {"r1": 1}, known, current="r2")["sha256"] == "r2"      # fits first: a rod without its bore never wins
    fresh = rods + [{"sha256": "r3", "mtime": 3}]
    assert choose(fresh, {}, known, current="r3")["sha256"] == "r3"            # the newest repair is tried once
    assert choose(fresh, {}, known, current="r2")["sha256"] == "r1"            # an old never-assembled one is not


def test_a_solidworks_lock_of_a_dead_process_is_released():
    import json, os, tempfile
    from pathlib import Path
    from app.agent.execution import cad_execution
    from app.agent.harness import HarnessStopped
    with tempfile.TemporaryDirectory() as tmp:
        lock, job = Path(tmp) / "sw.lock", Path(tmp) / "job"
        lock.write_text(json.dumps({"owner": "x", "pid": 2 ** 30}))                # no such process
        with cad_execution(job, "test", "h", lock_path=lock):
            assert json.loads(lock.read_text())["pid"] == os.getpid()
        assert not lock.exists()
        lock.write_text(json.dumps({"owner": "x", "pid": os.getpid()}))           # a live holder still blocks
        try:
            with cad_execution(Path(tmp) / "job2", "test", "h", lock_path=lock):
                raise AssertionError("a live lock must block")
        except HarnessStopped:
            pass


def test_a_reused_twin_assembly_belongs_to_the_new_result():
    import tempfile
    from pathlib import Path
    from unittest import mock
    from app.assembly.model import digest
    from app.web import server
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "web").mkdir()
        for rid, sec in (("aaaaaaaaaaaa", 1), ("bbbbbbbbbbbb", 2)):
            server.write_json(root / "web" / f"{rid}.json", {"id": rid, "seconds": sec, "pages": [{"sha": "s"}]})
        twin = {"id": "x", "result_id": "aaaaaaaaaaaa", "plan": {"source_hash": "old"}}
        server.write_json(root / "asm" / "aaaaaaaaaaaa" / "latest.json", twin)
        server.write_json(root / "parts" / "aaaaaaaaaaaa" / "batch.json", {"phase": "done", "error": "old"})
        with mock.patch.object(server, "RESULTS", root / "web"), mock.patch.object(server, "ASSEMBLIES", root / "asm"), \
                mock.patch.object(server, "PART_LISTS", root / "parts"):
            batch = server._reuse_twin("bbbbbbbbbbbb", "aaaaaaaaaaaa")
            copied = server.read_json(root / "asm" / "bbbbbbbbbbbb" / "latest.json")
            assert copied["result_id"] == "bbbbbbbbbbbb"
            assert copied["plan"]["source_hash"] == digest(server.assembly_source("bbbbbbbbbbbb"))
            assert "error" not in batch


def test_a_crossing_outline_is_refused_before_the_build():
    from app.geometry import outline_crossing
    square = [{"kind": "line", "x1": 0, "y1": 0, "x2": 10, "y2": 0}, {"kind": "line", "x1": 10, "y1": 0, "x2": 10, "y2": 10},
              {"kind": "line", "x1": 10, "y1": 10, "x2": 0, "y2": 10}, {"kind": "line", "x1": 0, "y1": 10, "x2": 0, "y2": 0}]
    assert outline_crossing(square) is None
    bow = [{"kind": "line", "x1": 0, "y1": 0, "x2": 10, "y2": 10}, {"kind": "line", "x1": 10, "y1": 10, "x2": 10, "y2": 0},
           {"kind": "line", "x1": 10, "y1": 0, "x2": 0, "y2": 10}, {"kind": "line", "x1": 0, "y1": 10, "x2": 0, "y2": 0}]
    i, j, (x, y) = outline_crossing(bow)
    assert (i, j) == (0, 2) and abs(x - 5) < 1e-9 and abs(y - 5) < 1e-9
    slot = [{"kind": "line", "x1": 0, "y1": 0, "x2": 20, "y2": 0}, {"kind": "arc", "cx": 20, "cy": 5, "x1": 20, "y1": 0, "x2": 20, "y2": 10, "dir": 1},
            {"kind": "line", "x1": 20, "y1": 10, "x2": 0, "y2": 10}, {"kind": "arc", "cx": 0, "cy": 5, "x1": 0, "y1": 10, "x2": 0, "y2": 0, "dir": 1}]
    assert outline_crossing(slot) is None                                   # tangent arcs meet lines at their ends only
