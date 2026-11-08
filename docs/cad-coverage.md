# CAD coverage baseline

13 sets; 11 real drawings; 0 verified drawings; 0 reviewed requirement sets; 0 offline-valid reference lists.

No live CAD coverage score is available. Menu membership is subject to each feature's limits. Draft requirements and synthetic variants are excluded from gap ranking. Offline eligibility does not prove drawing fidelity.

| Drawing | Truth | Requirements | Reference list | Missing capabilities (draft if unreviewed) |
|---|---|---|---|---|
| s01_sheet_metal_bracket | draft | pending review | missing | sheet_metal |
| s02_pipe_fitting | draft | pending review | missing | general_edges |
| s03_flange_hub | draft | pending review | missing | datum_planes, general_edges |
| s04_gearbox_housing | draft | pending review | missing | general_edges |
| s05_pipe_elbow | draft | pending review | missing | unassessed |
| s06_valve_body | draft | pending review | missing | general_edges, termination |
| s07_bent_bracket | draft | pending review | missing | datum_planes |
| s08_curved_lever | draft | pending review | missing | unassessed |
| s09_support_block | draft | pending review | missing | general_edges |
| s10_bracket_83 | draft | pending review | missing | general_edges |
| s11_triple_clamp | draft | pending review | missing | datum_planes, general_edges |
| m01_triple_clamp_2pages | draft | pending review | missing | unassessed |
| m02_pipe_fitting_2pages | draft | pending review | missing | unassessed |

## Gaps from reviewed requirements

No reviewed requirements yet; a ranked feature backlog would be speculative.

## Current menu limits

- **extrude_profile_boss**: the first item must be the base (no face); only one base; a curved outline whose arc end points are NOT printed (only radii, centres, 'tangent') is a `tangent_chain`; explicit `segments` need every end point printed or derived; a `tangent_chain` outline works on the front and back faces only; no draft, no taper
- **cut**: shapes must not overlap each other; a counterbore or countersink is a `hole`, not a cut; a shape's `pattern` works on the front and back faces and on a face placed with `on`; on the other faces of the base list every position; a circular pattern takes no `position`
- **hole**: countersink angle other than 90 is refused; no real threads: the thread note is only carried along; blind holes are flat bottomed; `through` cuts through ALL the material along the line: if solid lies beyond the thickness meant (for example the bore must stop at the open end of a bend), use a blind `depth` equal to that length instead of 'through'; `pattern` (linear, grid, circular, mirror) works on the front and back faces and on a face placed with `on`; on the other faces of the base list every position; a circular pattern takes no `positions`
- **circle_boss**: round bosses only: other boss shapes use extrude_profile_boss with a face; a circle's `pattern` works on the front and back faces and on a face placed with `on`; on the other faces of the base list every position; a circular pattern takes no `position`
- **profile_cut**: the outline must be closed and must not cross itself; a `tangent_chain` outline works on the front and back faces only
- **rib**: round boss only; ribs run along x (a rib along y, at an angle or with a curved or stepped profile is `unsupported`); straight sloped edge only, no fillet on the rib itself (a fillet at its foot is a fillet_chamfer_edges item); the rib must fit inside the plate outline and below the boss top
- **fillet_chamfer_edges**: chamfers are equal on both faces (two distances are not built); the edges must lie in the plane of one face of the base (front, back, top, bottom, left, right; `offset` for a flat face beyond it); use `along` to keep only the straight edges running one way, e.g. the corner edges through the thickness
- **revolve_base**: it can only be the FIRST item; after it NO other item can be built yet (cross holes, flats and end-face features are `unsupported`); full 360 degrees only; a groove or bore is drawn into the half-section, never cut afterwards; the part spans z = 0 .. 2 x the largest radius (the pipeline's back face is z = 0)
- **revolve_on_face**: the axis is vertical and lies in the plane of the face; an axis perpendicular to the face, a horizontal axis and a revolve CUT are `unsupported`; only 45, 90, 135 and 180 degrees are built; the section must lie clear of the axis (not touching it) and inside the base outline; the far end of a bend (for example a second flange) is a separate feature on another face and is not built by this one
- **turned_axial**: only after a revolve_base; round sections only, parallel to the part axis; a boss must touch the turned part (one body)
- **cross_hole**: only after a revolve_base; through holes only, crossing the axis at 90 degrees (no offset, no angle, no blind cross hole); the hole must stay between the ends of the part

## Execution prerequisites

- Third-party source present: True
- Default compiled server present: True
- These are file-presence checks only; they do not test SolidWorks installation, licensing, or custom executable paths.
