# Wankel engine sheets: what the CAD side can build (2026-11-08)

Source: `Wankel_Rotary_Engine_Engineering_Drawings_3_Pages.pdf`. Nothing here has run in SolidWorks (no licence); "offline" means the
build list validates, every feature compiles, and the feature graphs pass upstream's real IR validator.

## New: formula-defined outlines (`app/curves.py`)
Usable as a `profile` in `extrude_profile_boss` (base or boss) and `profile_cut`, on the front/back faces:

| shape | from the printed data | checked offline |
|---|---|---|
| `spur_gear` | z, m, alpha; `internal: true` for a ring gear (the toothed hole) | tip/root/pitch sizes, one tip + root arc per tooth, every chord within 5 um of the true involute, tooth = pi m / 2 on the pitch circle |
| `epitrochoid` | generating radius R, eccentricity e (+ printed clearance) | major axis 2(R+e), chords within 5 um of the true curve, R/e > 3 |
| `rotor_flank` | R, e (+ printed clearance) | apexes on R at 0/120/240 deg; in 37 positions around the shaft the rotor never cuts the housing and its apexes stay on it |

Curves are exact arcs where circular, short chords elsewhere (stated 0.005 mm modelling tolerance). Gear root fillets are NOT modelled and
are listed as an `unsupported` gap that a human must accept.

## Part by part
| Part | Status |
|---|---|
| 04 Stationary gear (z42 m2, Ø30 H7, 3x Ø8 PCD 64) | **Build list ready** (`eval/build_lists/wankel_stationary_gear.json`), offline valid; job waits for approval |
| 05 Internal ring gear (z84 m2, Ø198, 6x Ø8 PCD 186) | **Build list ready** (`eval/build_lists/wankel_internal_gear.json`), offline valid; job waits for approval |
| 01 Rotor | Flank generated, but **the sheet is inconsistent**: R65 / e15 give a 112.58 apex-to-apex rotor, the sheet prints 120 (and 90 height vs about 100). Needs the designer's answer before building. Apex/side/corner seal grooves are rotated slots (not yet buildable). |
| 02 Rotor housing | Trochoid bore buildable. The outer lobed outline is not fully dimensioned (lobe radii missing), the water jacket is an internal cast cavity (needs a shell/cavity feature), coolant ports Ø16 are side-face holes (buildable). |
| 03 Side housing | Outline, Ø45 H7 bore, 10x Ø10.5 PCD 170, ports (rounded rectangles R6/R8) buildable once the outer outline is written; the angled spark-plug boss and M14x1.25 thread are not. |
| 03 Eccentric shaft | **Build list ready** (`eval/build_lists/wankel_eccentric_shaft.json`): stepped shaft + Ø64 lobe at e = 15 with the new `turned_axial`; offline valid, job waits for approval. R6/R20 fillets at the lobe and the M24 threads are listed gaps. |
| Seals (apex/side/corner), dowel | Simple prisms/pins: buildable; 30° seal chamfers need general edge chamfers. |
| Hex bolts M10, oil seal | Purchased parts: supply models. |
| Assembly (page 3, 12 BOM rows) | `app/assembly/compose.py` can place them (shaft-axis concentric mates); the parts list quantities are printed. Not yet wired to the browser. |

## Next capabilities this sheet needs
1. ~~Features after a turned base~~ done: `turned_axial` (boss/cut along the axis, eccentric) and `cross_hole`. Revolve on other axes remains.
2. ~~Rotated slots / grooves~~ done (turned rectangles in cuts and outlines). Angled datum planes (spark-plug boss) remain.
3. A cavity/shell feature (water jacket) through our own C# extension.
4. ~~General edge fillets/chamfers~~ partly done: any face of the base, with an `along` direction filter (e.g. corner edges through the thickness). Edges on later features' faces and chamfers with two distances remain.
