# Build features (the menu the AI writes build lists from)

16 features, defined in `app/menu/registry.py`, built in `app/features/`. Each one goes to SolidWorks as a feature graph through the MCP
bridge's `submit_feature_graph` (or its low-level face tools), and each step is checked after it runs: the volume change against a value
computed from the printed sizes, the body count, and position probes. The catalogue the bridge publishes is
`storage/solidworks-tools.json` (`python scripts/setup_solidworks_backend.py --probe --catalog storage/solidworks-tools.json`).

## Start a part
| Feature | Part | Volume check |
|---|---|---|
| `extrude_profile_boss` | any flat outline pushed into a solid; outlines include rectangle, polygon, ring, lines + arcs, `spur_gear`, `epitrochoid`, `rotor_flank` | exact |
| `revolve_base` | a turned part from its half-section | exact (Pappus) |

## Whole parts (the only item of the list)
| Feature | Part | Volume check |
|---|---|---|
| `swept_tube` | a round bar or tube along straight runs and bends in the front view (handle, bent rod, pipe spool) | exact (section x centreline length) |
| `loft` | a blend between two sections on parallel planes: circle / rectangle, optional corner radius and shift (reducer, square-to-round, tapered post) | exact for circle-circle and sharp rectangle-rectangle; range otherwise |
| `sheet_metal_base` | a rectangular sheet blank with flanges on its edges (bracket, cover, tray) | exact for the blank; range with flanges |

## On a flat (extruded) base
`cut`, `hole`, `circle_boss`, `profile_cut`, `rib`, `fillet_chamfer_edges`, `revolve_on_face`

## On a turned base
| Feature | Adds | Volume check |
|---|---|---|
| `turned_axial` | a round boss or cut along the axis, optionally eccentric | exact |
| `cross_hole` | a through hole across the axis | exact |
| `radial_blades` | equal blades / fins round the axis: straight (fans, cooling fins) or single-arc impeller vanes from the inlet / outlet blade angles | exact |
| `turned_keyway` | a keyway along one round section, round or square ends | exact |

## Not built yet
Swept cut, shell / hollow, loft through more than two sections or on angled planes, holes in sheet metal, threads (notes only).
The volute of a pump casing needs a swept cut and angled reference planes in the C# bridge (`third_party/mcp-server-solidworks-main/execution`).

Nothing here has run in SolidWorks yet (no licence on the development machine): every feature compiles, passes the bridge's own feature-graph
validator, and matches its hand-calculated volume offline (`tests/test_*.py`).
