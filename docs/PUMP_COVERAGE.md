# Water pump sheets: what the CAD side can build (2026-11-10)

Source: `Water_Pump_Motor_Engineering_Drawings_3_Pages.pdf` (Single Stage Water Pump Motor, HUST, 3 sheets, 12 parts in the parts list).
Nothing here has run in SolidWorks (no licence). "Offline" means: the build list validates, every feature compiles, turned features pass
upstream's IR validator, and the model volume equals a hand calculation from the printed sizes (`tests/test_pump_lists.py`).
Holes and edge chamfers on flat parts are placed against real faces inside SolidWorks, so for those only the body is checked offline.

## Build lists written (eval/build_lists/pump_*.json)
| Part | Features | Offline result | Waits for the designer on |
|---|---|---|---|
| 02 Pump shaft (sheet 2) | `revolve_base`: 8 steps, M24 / M20 ends at major diameter, 2x45° both ends, R2.0 at the Ø50 collar | compiles, valid, 236,439 mm³ (hand: 236,244 + ~200 for the two R2 fillets) | callout lengths disagree with the chain (Ø35 seal seat L = 30 on a 42 step, Ø30 bearing seat L = 42 on a 30 step, Ø25 L = 30 on a 40 step); keyway 10x4.5 and threads are gaps |
| 07 Coupling half (sheet 2) | `revolve_base` (Ø70 x 12 flange, Ø46 x 28 hub, Ø30 H7 bore, 2x45°) + 4 `turned_axial` cuts (4x Ø10.5 on PCD Ø54) | compiles, valid, 59,853 mm³ = hand | confirm which side the 12 flange is on |
| 05 Mechanical seal seat (sheet 1) | `revolve_base` (Ø60 x 12, Ø50 body, bores Ø35 / Ø25 / Ø42) | compiles, valid, 52,359 mm³ = hand | confirm the Ø42 step and its length 6 (small 2:1 section) |
| 03 Suction flange detail (sheet 1) | ring Ø110 / Ø50 x 18, 4x Ø10.5 on PCD Ø90 (at 45°), 2x45° rim chamfer | body 135,717 mm³ = hand; holes and chamfer placed in SolidWorks | which edges get the chamfer |
| 04 Discharge flange detail (sheet 1) | ring Ø110 / Ø40 x 16, 4x Ø10.5 on PCD Ø80, 2x45° | body 131,947 mm³ = hand | same |
| 06 Shaft key (sheet 2) | block 40 x 10 x 4.5 | 1,800 mm³ = hand | end rounding drawn but not dimensioned (gap) |

The two flange details are details OF THE PUMP CASING's cast flanges, not separate parts: they show the flange features work; the casing
itself still needs its volute (below).

## Not buildable from this sheet alone
| Part | Why | What is needed |
|---|---|---|
| 01 Pump casing | cast volute (spiral passage growing around the impeller), discharge neck at an angle to the body | a sweep / loft feature; the volute's spiral is not dimensioned on the sheet either |
| 02 Motor housing | cylinder Ø140 x 168 with Ø150 end flanges, feet and stator cavity Ø120 are buildable; the cooling fins are not dimensioned, the lifting eye is a bought part | fin sizes: fins are now `radial_blades` (below) |
| 01 Impeller | hub and shrouds are a revolve (Ø140, Ø110, Ø50 eye, Ø70 hub), but the 7 curved blades are free-form | blades are now `radial_blades` arc (below); count, thickness and blade angles are not printed |
| 08 Cooling fan | hub Ø60 with Ø28 H7 bore, Ø160 x 32 is buildable; the radial blades are not dimensioned | blades are now `radial_blades` straight (below); count and thickness from the designer |
| 03 Bearing housing / end cover | the view mixes a section with the bolt pattern: 4x Ø10.5 "PCD Ø90" but the holes are drawn 88 apart on a 110 x 106 square outline (a PCD of about 124) | the designer's answer on the hole pattern |
| Stator, rotor (9, 10) | shown only inside the assembly section, no sizes | detail drawings |

## New custom features (2026-11-10): `radial_blades` and `turned_keyway`
Added to the build menu, so to the MCP catalogue (`storage/solidworks-tools.json`, regenerated through the MCP handshake: 11 feature graphs valid).
Both go to SolidWorks as feature graphs (sketch + extrude) through the existing `submit_feature_graph` tool; no change to the C# bridge.

| Feature | Builds | Checked offline |
|---|---|---|
| `radial_blades` | N equal blades / fins round a turned part, standing on the hub or spanning two shrouds: straight (fan blades, cooling fins) or curved impeller vanes as the textbook single-arc blade from the inlet / outlet blade angles | blade angles hit the requested beta 1 / beta 2 exactly; areas exact (straight: closed form, arc: dense numeric); refuses overlapping blades, blades cutting existing material, floating blades |
| `turned_keyway` | a keyway width x depth along the top of one round section, round or square ends | volume = closed form (square ends) and brute force within 0.1 mm³ (round ends); refuses a keyway across a step |

What they unlock here, once the designer gives the numbers the sheet does not print:
| Part | Feature | Designer must give |
|---|---|---|
| 08 Cooling fan | `radial_blades` straight on the Ø60 hub out to Ø160 | blade count (12 are drawn) and thickness |
| 01 Impeller | `radial_blades` arc between the shrouds | blade count (7 drawn), thickness, inlet / outlet blade angles; and the shroud / hub section (Ø110, Ø140, Ø50 eye vs Ø50 bore, hub t = 16 within a 36 overall width do not close) |
| 02 Motor housing fins | `radial_blades` straight on the Ø140 body | fin count, thickness, height |
| 02 Pump shaft keyway | `turned_keyway` 10 x 4.5 on the Ø50 seat | keyway length (key 40 vs seat 38) |
`tests/test_blades.py` builds the fan and the keyway with sample designer answers to prove the path end to end.

## Still not buildable: the pump casing volute
The volute is a passage whose cross-section GROWS round the impeller. The SolidWorks bridge's feature graph has a swept BOSS only (no swept cut,
no shell, no loft between angled planes: sketches go on the three datum planes and offsets of them). A hollow volute therefore needs new
operations in the C# executor (`third_party/mcp-server-solidworks-main/execution/solidworks`): a swept cut and reference planes at an angle.
The sheet does not dimension the volute either (section sizes round the spiral, cutwater position).

## Bought parts
Bearings DE / NDE (6306), mechanical seal, eye bolt: supply SolidWorks models. Note: the sheet prints 6306 as Ø30 x Ø62 x 16, which are the
sizes of a 6206; a 6306 is Ø30 x Ø72 x 19. Worth confirming before ordering.

## Fixed while testing
The extraction read the parts list's material column into the part name ("Pump Casing — Cast Iron (FC250)"), so the SolidWorks page found
no views for 11 of 12 parts. Names are now split at the dash (material kept separately) and the mechanical seal counts as bought.
