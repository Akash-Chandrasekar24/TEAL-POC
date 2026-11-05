# Reviewed 3D models and native 2D sheets

The new sheet workflow creates native `.SLDDRW` and PDF output **from an existing,
reviewed 3D model**. It does not infer missing geometry from a screenshot, and it
does not recreate every original annotation, section/detail view or BOM layout.

It supports standard front/top/right/left/back/bottom/isometric views on one A3
landscape sheet. Every view has an explicit position, scale and model
configuration. First/third-angle placement is checked against the native sheet's
projection metadata. Configure the drawing template to the intended projection;
a mismatch stops the workflow rather than silently changing the drawing.

Source parts must be saved SLDPRT files with a reviewed SHA-256. Source assemblies
must come from an **accepted existing assembly job**, with unchanged approved
plan/report, native model and bundled component hashes. The drawing inspector
also reads each view's component reference/configuration/count from SolidWorks.
Nested, suppressed or unresolved references stop this first implementation.

## Approval workflow

```powershell
.venv\Scripts\python.exe scripts/run_sheet.py schema
.venv\Scripts\python.exe scripts/run_sheet.py check sheet-plan.json
.venv\Scripts\python.exe scripts/run_sheet.py start sheet-plan.json
.venv\Scripts\python.exe scripts/run_sheet.py approve JOB_ID
# Only with licensed SolidWorks available:
.venv\Scripts\python.exe scripts/run_sheet.py execute JOB_ID --solidworks
# Compare the sheet with its source drawings and the reviewed 3D model:
.venv\Scripts\python.exe scripts/run_sheet.py accept JOB_ID
```

The plan requires source evidence, native dimension names, expected SI values and
tolerances, plus a binding of each expected dimension to a view. Native length
values are metres; angle values use the native dimension's SI convention. These
bindings must come from the reviewed model/source, not guessed pixel distances.
The schema command displays the exact contract. Unsupported requirements must
remain in `unsupported_requirements` and block approval. A source needing section
A-A, BOM balloons, original notes/tolerances or other unimplemented treatments
must not be represented as a complete reproduction using standard views alone.

Jobs are saved under `storage/sheets/jobs/`. Approval locks the exact plan.
Execution shares the project-wide CAD writer lease and native intent journal,
never closes existing user documents, and makes no model calls. A failed or
incomplete inspection prevents export. The finished sheet awaits human review;
modified reports or output files cannot be accepted under the original checks.

## Native checks

The owned `inspect_drawing` MCP endpoint is compiled into the fingerprinted build
copy, leaving the pinned upstream checkout unchanged. It reads:

- Active drawing identity, sheet count, paper size, projection and rebuild result.
- Exact referenced model paths/configurations, including accepted assembly leaves.
- Model view orientation, scale, position and outline in sheet coordinates.
- Native displayed dimension names and their SI values.

The Python runner checks required dimensions, uniqueness, view bounds and
nonoverlap, then repeats checks after native save and PDF export. It checks
output presence and PDF header and verifies that the reviewed source files did
not change. View outlines do not certify annotation spacing or readability; those
remain part of the final source/visual review.

`accuracy_verified` remains false. A completed checked drawing is evidence that
the supplied plan/model was followed, not proof that a raster drawing's hidden
geometry, manufacturing intent or every callout was recovered.

## Supplied drawing assessment

Use the browser's **Check reconstruction readiness** to identify component scope,
unreadable information and missing application capabilities. The normal whole-page
reader now also reports document kind and unreadable callout locations. An
assembly/detail sheet cannot enter the single-part compiler as one plate. Missing
printed values remain review blockers; agreeing model reads do not establish
engineering accuracy. The separate assembly workflow still requires component
models, reviewed quantities, placements and mates.

The test suite can assess independent source files and then run actual extraction:

```powershell
.venv\Scripts\python.exe scripts/check_drawings.py --inputs inputs --files "image copy 2.png" "image copy 3.png" "image copy 4.png" "image copy.png" "image.png" --vision --read --out storage/acceptance/20261104-inputs
```

This groups exact duplicate inputs and writes `summary.json`, `report.md` and
per-source traces. It never treats different files as views of one physical part,
starts SolidWorks or awards a geometric accuracy score. Existing benchmark truths
remain drafts pending engineering review.
