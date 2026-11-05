"""Sub-assemblies and patterns compose into the flat plan the runner executes, using SolidWorks' row-vector transform convention (p' = p·M + t)."""
import math

import fakes  # noqa: F401
from app.assembly.compose import Instance, Pattern, SubAssembly, compose
from app.assembly.model import AssemblyPlan, Mate, Occurrence, Placement, Surface, validate_plan

R90Z = (0, 1, 0, -1, 0, 0, 0, 0, 1)   # 90 degrees about +z, ROW convention: the x axis maps to +y (first row)


def plane(occ, x=0.0):
    return Surface(occurrence=occ, kind="plane", point=(x, 0, 0), direction=(1, 0, 0))


def place(t, rotation=(1, 0, 0, 0, 1, 0, 0, 0, 1), confirmed=True):
    return Placement(translation=t, rotation=rotation, evidence="test", confirmed=confirmed)


def bracket():
    return SubAssembly(name="bracket", occurrences=[Occurrence(id="base", bom_id="B1", fixed=True, placement=place((0, 0, 0))),
                                                    Occurrence(id="arm", bom_id="B2", placement=place((10, 0, 0)))],
                       mates=[Mate(id="m", type="lock", a=plane("base", 10), b=plane("arm"), evidence="test", confirmed=True)])


def close(a, b):
    return all(abs(x - y) < 1e-9 for x, y in zip(a, b))


def test_translation_only_instances_compose_to_the_instance_offset():
    occ, mates, blockers = compose([bracket()], [Instance(id="L", of="bracket", placement=place((100, 0, 0)), fixed=True),
                                                 Instance(id="R", of="bracket", placement=place((0, 50, 0)))])
    assert blockers == [] and [o.id for o in occ] == ["L.base", "L.arm", "R.base", "R.arm"]
    assert close(occ[1].placement.translation, (110, 0, 0)) and close(occ[3].placement.translation, (10, 50, 0))
    assert [o.fixed for o in occ] == [True, False, False, False]          # an unfixed instance has nothing fixed
    assert [m.id for m in mates] == ["L.m", "R.m"] and mates[1].a.occurrence == "R.base" and mates[1].b.occurrence == "R.arm"
    assert mates[0].a.point == (10, 0, 0)                                   # surfaces stay component-local


def apply(p, m, t):
    """SolidWorks row-vector transform: p' = p·M + t."""
    return tuple(sum(p[k] * m[3*k+j] for k in range(3)) + t[j] for j in range(3))


def test_rotated_instance_composes_as_row_vectors_and_equals_applying_the_two_transforms_in_turn():
    local = place((10, 5, 2), (0, 0, 1, 1, 0, 0, 0, 1, 0))        # a non-trivial local rotation (axes permuted)
    group = SubAssembly(name="g", occurrences=[Occurrence(id="o", bom_id="B1", fixed=True, placement=local)])
    inst = Instance(id="X", of="g", placement=place((3, -4, 7), R90Z), fixed=True)
    occ, _, blockers = compose([group], [inst])
    assert blockers == [] and close(occ[0].placement.translation, (-2, 6, 9))       # local origin of the part, seen from the assembly
    for p in ((1, 0, 0), (0, 2, 0), (0, 0, 3), (1.5, -2, 4)):                         # any point of the part: composed == local then instance
        step = apply(apply(p, local.rotation, local.translation), inst.placement.rotation, inst.placement.translation)
        assert close(apply(p, occ[0].placement.rotation, occ[0].placement.translation), step)
    assert close(apply((1, 0, 0), R90Z, (0, 0, 0)), (0, 1, 0))                         # the convention itself: x maps to +y


def test_circular_pattern_places_copies_on_the_circle_and_templates_mates():
    template = Mate(id="to_hub", type="distance", value=0, a=plane("@"), b=plane("hub"), evidence="test", confirmed=True)
    pattern = Pattern(id="bolt", kind="circular", bom_id="BOLT", base=place((10, 0, 0)), count=4, center=(0, 0, 0), axis=(0, 0, 1), angle=90, mates=[template])
    occ, mates, blockers = compose([], [], [pattern])
    assert blockers == [] and [o.id for o in occ] == ["bolt.1", "bolt.2", "bolt.3", "bolt.4"]
    assert [tuple(round(x, 9) for x in o.placement.translation) for o in occ] == [(10, 0, 0), (0, 10, 0), (-10, 0, 0), (0, -10, 0)]
    assert [m.id for m in mates] == [f"bolt.{k}.to_hub" for k in (1, 2, 3, 4)] and mates[2].a.occurrence == "bolt.3" and mates[2].b.occurrence == "hub"
    # orientation follows the same rotation: copy 2's x axis (first row) points where the circle's tangent does
    assert close(occ[1].placement.rotation, R90Z)


def test_linear_pattern_needs_no_convention_check_and_steps_in_plan_units():
    occ, _, blockers = compose([], [], [Pattern(id="row", kind="linear", bom_id="P", base=place((5, 0, 0)), count=3, step=(0, 20, 0))])
    assert blockers == [] and [o.placement.translation for o in occ] == [(5, 0, 0), (5, 20, 0), (5, 40, 0)]


def test_bad_inputs_are_blockers_not_guesses():
    _, _, blockers = compose([bracket()], [Instance(id="A", of="nope", placement=place((0, 0, 0)))])
    assert blockers == ["A: unknown sub-assembly nope"]
    bad = Mate(id="x", type="lock", a=plane("hub"), b=plane("other"), evidence="t", confirmed=True)       # no '@' end
    _, _, blockers = compose([], [], [Pattern(id="row", kind="linear", bom_id="P", base=place((0, 0, 0)), count=2, step=(1, 0, 0), mates=[bad])])
    assert any("exactly one end named '@'" in b for b in blockers)
    _, _, blockers = compose([bracket()], [Instance(id="A", of="bracket", placement=place((0, 0, 0))), Instance(id="A", of="bracket", placement=place((9, 0, 0)))])
    assert any("Duplicate generated occurrence id" in b for b in blockers)
    try:
        Pattern(id="p", kind="circular", bom_id="P", base=place((1, 0, 0)), count=3)
        assert False, "circular pattern without axis"
    except ValueError:
        pass


def test_unconfirmed_placements_stay_unconfirmed_after_composition():
    occ, _, _ = compose([bracket()], [Instance(id="L", of="bracket", placement=place((0, 0, 0), confirmed=False))])
    assert all(o.placement.confirmed is False for o in occ)       # the reviewer must confirm the instance placement; composition never confirms


def test_composed_output_passes_the_existing_plan_validator():
    occ, mates, blockers = compose([bracket()], [Instance(id="L", of="bracket", placement=place((0, 0, 0)), fixed=True),
                                                 Instance(id="R", of="bracket", placement=place((0, 50, 0)))])
    link = Mate(id="link", type="lock", a=plane("L.base"), b=plane("R.base"), evidence="test", confirmed=True)   # external mate joins the unfixed instance
    row = lambda i, pn: {"id": i, "part_number": pn, "description": pn, "quantity": 2, "kind": "part", "evidence": [{"page": 1, "text": pn}], "confirmed": True}
    model = lambda i: {"bom_id": i, "sha256": "a" * 64, "source_name": f"{i}.SLDPRT", "configuration": "Default", "provenance": "TEST", "confirmed": True}
    plan = AssemblyPlan.model_validate({"source_hash": "t", "unit": "mm", "unit_confirmed": True, "bom_complete": True,
                                        "bom": [row("B1", "BASE"), row("B2", "ARM")], "models": [model("B1"), model("B2")],
                                        "occurrences": [o.model_dump() for o in occ], "mates": [m.model_dump() for m in [*mates, link]],
                                        "expected": {"size": [20, 60, 10], "size_evidence": "test"}})
    assert blockers == [] and validate_plan(plan) == []            # no assets dir: file checks skipped
