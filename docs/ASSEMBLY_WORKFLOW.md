# Assembly workflow

This is the first flat-assembly pipeline, separate from the existing part agent.
It combines the pinned upstream insertion/mating tools with application-owned
evidence, component storage, approval, and runtime verification. It does not
reconstruct a complete pump skid accurately from a screenshot alone.

## Available now

- Read the BOM from **whole sheets**, including tables missed by view cropping.
  Repeated scope/item rows merge their evidence without adding quantities.
  Conflicting quantities, identities or descriptions remain review blockers.
- Review completeness and units explicitly. Keep the original extraction beside
  each immutable reviewed snapshot. Source changes invalidate old reviews.
- Import native SLDPRT files into SHA-256-addressed storage. Map each BOM row to
  a reviewed model/configuration and source evidence. Purchased models and native
  parts built with the existing custom feature workflow use the same path.
- Define explicit occurrences, proper rigid transforms, fixed references and
  eight upstream mate types. Select planar/cylindrical faces by their analytic
  geometry in component-local coordinates. Zero or multiple matches stop.
- Validate exact BOM quantities, identities, placement evidence, source hashes,
  model mappings, mate connectivity and expected overall dimensions before
  approval. A graph connection alone does not prove full constraint; the live
  solver check is separate.
- Persist approval of the exact snapshot. Execution is a separate explicit
  command. Check positions/rotations after every insertion and mate, verify
  native mate readbacks, rebuild errors, constraint status, geometry bounds and
  interference before native/STEP export. Bundle the exact component files.
- Keep the finished run awaiting human drawing/visual review. Changed outputs
  cannot be accepted under the original report.

## Browser

Start the existing local reader:

```powershell
.venv\Scripts\python.exe -m app.web.server
```

Upload the drawing sheets/PDF in the reader, then choose **Open assembly planner**
below the result. This also works for results generated without dimension reading.
The assembly reader makes one vision call per available whole sheet. Missing
page images remain blockers. Model calls need a configured provider, but model
upload, review, validation, downloads and CLI approval do not need SolidWorks.

The assembly page currently uses an **advanced JSON editor** for review and CAD
parameters. It is not a graphical mate/placement editor. Use **Add missing
occurrence placeholders** to create occurrence IDs from BOM quantities; these
have no guessed positions. Enter the translation, rotation, dimensional evidence
and confirmation for every instance. Set `unit_confirmed` and `bom_complete`
only after checking all sheets. Keep unresolved `issues` until they are resolved.

Upload each source model, supply its exact configuration and provenance, then
confirm its identity/dimensions in the plan. For a custom component, first build
and review the part with the existing part job, then attach its SLDPRT output.
Selecting `custom_features` records provenance; it does not fabricate geometry.

Save the review to see all blockers. Downloading an incomplete draft is allowed
so information can be collected offline. It cannot be approved or executed until
all blockers are resolved. Editing the browser draft hides its previous download
until the new review is saved. Reopen a snapshot with its page URL/hash.

## CLI

```powershell
# Upload a supplied model without the browser (returns its SHA-256/name).
.venv\Scripts\python.exe scripts/run_assembly.py import-model pump.SLDPRT

# Show the exact JSON contract or validate a reviewed draft.
.venv\Scripts\python.exe scripts/run_assembly.py schema
.venv\Scripts\python.exe scripts/run_assembly.py check assembly-plan.json

# Save a job, inspect its plan.json/blockers, and explicitly approve it.
.venv\Scripts\python.exe scripts/run_assembly.py start assembly-plan.json
.venv\Scripts\python.exe scripts/run_assembly.py approve JOB_ID
.venv\Scripts\python.exe scripts/run_assembly.py status JOB_ID

# Only when a licensed SolidWorks installation is available:
.venv\Scripts\python.exe scripts/run_assembly.py execute JOB_ID --solidworks
# Compare the resulting assembly and report with all source drawings, then:
.venv\Scripts\python.exe scripts/run_assembly.py accept JOB_ID
```

Storage is under `storage/assembly/`: `models/`, immutable browser `drafts/`,
and persistent `jobs/`. An unapproved job's `plan.json` may be completed and then
approved; changes after approval require a new job. Each execution gets its own
output directory and report. The CLI uses one execution lock. After an abnormal
process termination, inspect the job and SolidWorks state before removing a stale
`storage/assembly/jobs/execution.lock`. Do not run concurrent part/assembly jobs
against the same desktop instance. Execution requires an empty document workspace
and never closes existing documents automatically. No automatic repair is used.

## Coordinates and verification

All plan lengths use `mm` or `inch`, areas use the squared unit, and mate angles
use degrees. Placement rotation is SolidWorks MathTransform's row-major nine
values, followed at the MCP boundary by translation in metres and scale=1.
Reflections, scale, shear and non-finite values are refused. Surface directions
are unit vectors; surface points belong to the source part's coordinates.

Planar selectors compare plane offset/direction. Cylinder selectors compare axis
line/radius/direction. An optional area can distinguish otherwise repeated faces;
it remains subject to the upstream reader's rounding. Surface locations and areas
are rounded upstream to 1e-6 SI, so selectors cannot promise finer resolution.
Ambiguity always blocks. Mate readbacks must match names, types, alignment,
connected components and driving dimensions. `closest` alignment may resolve to
an explicit alignment in a live solver; if the exact readback differs, the run
stops for review. Use reviewed explicit alignment when possible.

The owned `inspect_assembly` MCP tool uses the native
`extensions/solidworks/AssemblyInspection.cs` endpoint. It attaches to an existing
instance on upstream's STA thread; it does not start COM. Verification rebuilds
the active assembly and uses transformed temporary body copies and extreme-point
queries for bounds. It refuses unresolved/suppressed/nested components, external
model references, missing bodies and incomplete reads. Source files remain pinned.
The executable's build manifest fingerprints this extension as well as upstream.

Interference detection includes hidden bodies, ignored interferences and bodies
within multibody parts. Coincident contact is excluded. Any reported interference,
including a possible interference, blocks export; intentional press/interference
fits currently need a future explicitly reviewed exception mechanism. These
checks follow the SolidWorks APIs for [constraint status](https://help.solidworks.com/2022/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IComponent2~GetConstrainedStatus.html)
and [interference options](https://help.solidworks.com/2022/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IInterferenceDetectionMgr_members.html).

## Validation and remaining work

Offline tests use **simulated CAD outputs**, not geometric reference models.
They exercise repeated/conflicting BOM rows, missing inputs, rigid transforms,
model tampering, persistent approval/review, face ambiguity, solver drift,
wrong configurations, incomplete inspection, interference, HTTP and UI behavior.
The C# extension is compiled against the installed interop assemblies, and MCP
discovery can be checked without starting the native executable or SolidWorks:

```powershell
.venv\Scripts\python.exe tests/run_all.py
node tests/check_assembly_ui.cjs
.venv\Scripts\python.exe scripts/setup_solidworks_backend.py --probe
```

**Live assembly reconstruction and accuracy have not been demonstrated.** A
SolidWorks license, native supplier/custom models, reviewed dimensional inputs
and reference assemblies are still required for real geometric acceptance tests.
Remaining capabilities include automatic balloon/view matching, generating
fabricated part plans from detail sheets, supplier model retrieval, native STEP
import, nested/flexible assemblies, piping routing, weldment/sheet-metal modeling,
visual reprojection against each drawing view, clearance/GD&T checks, and a
graphical placement/mate editor. A passing current report is a checked build
against the supplied plan, not proof that every hidden design detail was recovered.
