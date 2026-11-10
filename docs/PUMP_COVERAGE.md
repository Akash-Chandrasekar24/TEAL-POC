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

## Not buildable yet
| Part | Why | What is needed |
|---|---|---|
| 01 Pump casing | cast volute (spiral passage growing around the impeller), discharge neck at an angle to the body | a sweep / loft feature; the volute's spiral is not dimensioned on the sheet either |
| 02 Motor housing | cylinder Ø140 x 168 with Ø150 end flanges, feet and stator cavity Ø120 are buildable; the cooling fins are not dimensioned, the lifting eye is a bought part | a decision to model it without fins, or fin sizes |
| 01 Impeller | hub and shrouds are a revolve (Ø140, Ø110, Ø50 eye, Ø70 hub), but the 7 curved blades are free-form | a blade (sweep along a curve) feature; blade curve and count are not dimensioned |
| 08 Cooling fan | hub Ø60 with Ø28 H7 bore, Ø160 x 32 is buildable; the radial blades are not dimensioned | blade count/thickness from the designer |
| 03 Bearing housing / end cover | the view mixes a section with the bolt pattern: 4x Ø10.5 "PCD Ø90" but the holes are drawn 88 apart on a 110 x 106 square outline (a PCD of about 124) | the designer's answer on the hole pattern |
| Stator, rotor (9, 10) | shown only inside the assembly section, no sizes | detail drawings |

## Bought parts
Bearings DE / NDE (6306), mechanical seal, eye bolt: supply SolidWorks models. Note: the sheet prints 6306 as Ø30 x Ø62 x 16, which are the
sizes of a 6206; a 6306 is Ø30 x Ø72 x 19. Worth confirming before ordering.

## Fixed while testing
The extraction read the parts list's material column into the part name ("Pump Casing — Cast Iron (FC250)"), so the SolidWorks page found
no views for 11 of 12 parts. Names are now split at the dash (material kept separately) and the mechanical seal counts as bought.
