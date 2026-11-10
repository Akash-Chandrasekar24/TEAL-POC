# Build features (the menu the AI writes build lists from)

19 features, defined in `app/menu/registry.py`, built in `app/features/`. Each one goes to SolidWorks as a feature graph through the MCP
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
`cut`, `hole`, `circle_boss`, `profile_cut`, `rib`, `fillet_chamfer_edges`, `revolve_on_face`, and:

| Feature | Adds | Volume check |
|---|---|---|
| `angled_boss` | a round boss at an angle to the front face (spark-plug boss, angled port), optional hole down its axis | exact (boss pi r^2 h; blind hole pi r_h^2 depth) |

## On any base
| Feature | Adds | Volume check |
|---|---|---|
| `swept_passage` | a round passage CUT (or rod boss) along straight runs + bends, or a spiral that winds outward (pump volute, gallery, channel); a spiral may WIDEN (`end_diameter`), built as one sweep per quarter turn | upper bound (per step) |
| `shell` | hollows a plain box or cylinder to a wall thickness, chosen faces open (second item only) | exact |

## On a turned base
| Feature | Adds | Volume check |
|---|---|---|
| `turned_axial` | a round boss or cut along the axis, optionally eccentric | exact |
| `cross_hole` | a through hole across the axis | exact |
| `radial_blades` | equal blades / fins round the axis: straight (fans, cooling fins) or single-arc impeller vanes from the inlet / outlet blade angles | exact |
| `turned_keyway` | a keyway along one round section, round or square ends | exact |

## Our own SolidWorks operations
`swept_passage`, `shell` and `angled_boss` use four operations the upstream bridge does not have, written in
`extensions/solidworks/GeometryExtensions.cs` (compiled into the bridge by `scripts/build_execution_layer.py`, published as MCP tools by
`app/mcp_client/assembly_tools.py`): `ext_sweep` (swept boss or CUT), `ext_shell`, `ext_angled_plane` (reads the new plane's real normal and
rebuilds it flipped if needed) and `ext_sketch_extrude` (any plane, both ways, direction given as a vector). All coordinates are sent in model
space and converted with SolidWorks' own sketch transform. The MCP handshake lists 53 tools (47 upstream + 6 ours), no parameter drift.

## Not built yet
A volute that widens SMOOTHLY (swept_passage steps once per quarter turn), shells on bases with other features, loft through more than
two sections, holes in sheet metal, threads (notes only).

Nothing here has run in SolidWorks yet (no licence on the development machine): every feature compiles, passes the bridge's own feature-graph
validator, and matches its hand-calculated volume offline (`tests/test_*.py`).
