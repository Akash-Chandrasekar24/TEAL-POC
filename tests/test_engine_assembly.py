"""The 4-cylinder engine sheet (inputs/image_12_engine_assembly.png, six parts) as a structure test for sub-assemblies and patterns.
Placements here are SYNTHETIC test numbers, not read from the drawing; only the Ø33.8 pin radius and the part names are printed on the sheet."""
import fakes  # noqa: F401
from app.assembly.compose import Instance, SubAssembly, compose
from app.assembly.model import AssemblyPlan, Mate, Occurrence, Placement, Surface, validate_plan

PITCH = 84.0   # TEST ONLY: cylinder spacing along the crankshaft is not stated on the sheet
PARTS = [("B1", "01-PISTON", 4), ("B2", "02-PISTON-RING", 4), ("B3", "03-CRANKSHAFT", 1), ("B4", "04-CONNECTING-ROD", 4),
         ("B5", "05-CONNECTING-ROD-CAP", 4), ("B6", "06-PISTON-PIN", 4)]


def place(t, confirmed=True):
    return Placement(translation=t, evidence="TEST ONLY synthetic", confirmed=confirmed)


def pin_bore(occ):   # the printed diameter 33.8 -> radius 16.9, shared by piston bore, rod small end and pin
    return Surface(occurrence=occ, kind="cylinder", point=(0, 0, 0), direction=(0, 0, 1), radius=16.9)


def cylinder_group():
    ids = {"piston": "B1", "ring": "B2", "rod": "B4", "cap": "B5", "pin": "B6"}
    return SubAssembly(name="cylinder", occurrences=[Occurrence(id=k, bom_id=v, placement=place((0, 0, 10 * i))) for i, (k, v) in enumerate(ids.items())],
                       mates=[Mate(id="pin_in_piston", type="concentric", a=pin_bore("pin"), b=pin_bore("piston"), evidence="Ø33.8 on piston and pin", confirmed=True),
                              Mate(id="rod_on_pin", type="concentric", a=pin_bore("rod"), b=pin_bore("pin"), evidence="Ø33.8 on rod and pin", confirmed=True)])


def engine():
    inst = [Instance(id=f"C{n}", of="cylinder", placement=place((PITCH * (n - 1), 0, 0))) for n in (1, 2, 3, 4)]
    occ, mates, blockers = compose([cylinder_group()], inst)
    occ.append(Occurrence(id="crank", bom_id="B3", fixed=True, placement=place((0, 0, 0))))
    row = lambda i, pn, q: {"id": i, "part_number": pn, "description": pn, "quantity": q, "kind": "part", "evidence": [{"page": 1, "text": pn}], "confirmed": True}
    model = lambda i: {"bom_id": i, "sha256": "a" * 64, "source_name": f"{i}.SLDPRT", "configuration": "Default", "provenance": "TEST", "confirmed": True}
    return blockers, AssemblyPlan.model_validate({
        "name": "four_cylinder_engine", "source_hash": "engine-sheet-2", "unit": "mm", "unit_confirmed": True, "bom_complete": True,
        "bom": [row(i, pn, q) for i, pn, q in PARTS], "models": [model(i) for i, _, _ in PARTS],
        "occurrences": [o.model_dump() for o in occ], "mates": [m.model_dump() for m in mates],
        "expected": {"size": [336, 100, 100], "size_evidence": "TEST ONLY"}})


def test_four_cylinders_compose_to_21_occurrences_and_8_mates_with_the_right_quantities():
    blockers, plan = engine()
    assert blockers == [] and len(plan.occurrences) == 21 and len(plan.mates) == 8
    assert sorted(o.id for o in plan.occurrences if o.id.startswith("C2.")) == ["C2.cap", "C2.pin", "C2.piston", "C2.ring", "C2.rod"]
    assert [o.placement.translation[0] for o in plan.occurrences if o.id.endswith(".piston")] == [0, 84, 168, 252]
    assert all(m.a.occurrence.split(".")[0] == m.b.occurrence.split(".")[0] for m in plan.mates)   # each mate stays inside its cylinder


def test_the_sheet_alone_cannot_connect_the_pistons_to_the_crankshaft():
    """The rod big end has no stated journal diameter, so the plan must stay blocked rather than invent a mate."""
    _, plan = engine()
    blockers = validate_plan(plan)
    assert sum("no mate connection to a fixed reference" in b for b in blockers) == 20     # every cylinder part floats
    assert not any("quantity" in b for b in blockers)                                         # BOM counts match the composed occurrences
