# Complex-part development

The separate first assembly pipeline now lives in `app/assembly/`, with browser
BOM/model review, explicit placements/mates, persistent approval, and a native
verification extension. See [Assembly workflow](ASSEMBLY_WORKFLOW.md) for scope,
commands and remaining gaps. Offline/simulated checks do not establish real CAD
accuracy; live reference-assembly validation is still outstanding.

## Current implementation increment

The existing builder remains the production execution path. This increment adds:

- `eval/cad.py`: reproducible CAD capability inventory based on the actual menu,
  benchmark verification flags, requirement annotations, and reference build lists.
- `eval/cad_requirements.json`: a review queue for drawing requirements. Missing
  annotations mean unassessed, not supported. Draft annotations never rank gaps.
- `app/features/frames.py`: validated rigid sketch frames with arbitrary orientation,
  world/local transforms, coplanarity checks, and orientation mapping.
- The existing datum, face-cut, profile-cut and face-boss paths use this frame layer.
  Arc sweep is mapped by orientation, including major arcs and coincident endpoints.
  Scaled, sheared, non-finite, tilted or displaced face-frame readbacks are refused.

These changes do not expose arbitrary datum placement in the build-list schema or
enable operations after a turned base. Those changes require execution work and
live regression cases. The reviewed benchmark and reference CAD models are still
outstanding; this is a baseline, not a completed coverage assessment.

Run from the repository root:

```powershell
.venv\Scripts\python.exe -m eval.cad --out docs/cad-coverage.md
.venv\Scripts\python.exe -m eval.cad --json --out storage/cad-coverage.json
.venv\Scripts\python.exe tests\run_all.py
```

The checked-in coverage Markdown is a snapshot; regenerate it after changing
requirements, reference lists, feature limits, or benchmark verification.

## M1: representative CAD benchmark

There are 11 real drawings and two synthetic multipage variants in the current
benchmark. All drawing truths are draft and all reference lists are absent.
Do not count synthetic variants as additional independent part designs.

For each real drawing:

1. Review dimensions, projection and views in `eval/benchmark.json` with an engineer.
2. Add an assessment in `eval/cad_requirements.json`: `id`, `reviewed`,
   `required_capabilities`, and `evidence`. Evidence should identify views or sections
   supporting the required geometry. Use menu names for existing operations and
   names from `eval.cad.PLANNED` for missing capabilities. Alternative construction
   strategies should be considered before declaring an operation essential.
3. Add a reference build list (inline or a repository-relative JSON path in the
   benchmark's `build_list`). Keep unsupported geometry explicit. Validation must
   not be bypassed to obtain a passing score.
4. Record engineer-approved dimensions, body count, section checks, and reference
   CAD where available. Live measurements belong in run reports; this inventory
   deliberately makes no live-coverage claim.
5. Expand to 20–30 representative real parts based on actual intended use.

Exit: reviewed requirement coverage and ranked gaps from independent part designs,
with explicit missing dimensions and unsupported operations. Current ranking is
not available until requirements have been reviewed.

## M2: placement and turned-part follow-ups

Implementation order:

1. **Rigid coordinate math** — implemented and integrated in current face sketches.
2. **Semantic planes and axes** — add typed references with source feature, origin,
   basis, unit, and selection intent. Resolve actual trimmed faces with uniqueness
   checks; a plane alone does not prove a point lies within a face.
3. **Rotated placement execution** — add schema, menu adapter and a supported
   execution operation. Read back the actual sketch frame and compare it with
   the planned reference before adding entities. Do not project across planes.
4. **Turned end faces** — publish end references from the actual half-section for
   both horizontal and vertical axes. A bounding-box face is not sufficient: a
   stepped or hollow profile can have multiple disjoint end regions.
5. **Cross holes, flats and keyways** — require explicit datum/axis references and
   bounded termination. Verify location, axis, removal, and unaffected geometry.

Live acceptance matrix:

| Case | Required evidence |
|---|---|
| Six axis-aligned faces | Asymmetric centres, normals and extents match mm/inch requests |
| Inclined plane | Hole axis and entry/exit positions match the plan |
| Horizontal/vertical turned bases | Correct end chosen on solid and bored profiles |
| Stepped shaft | End references use actual end geometry, not maximum-radius bounds |
| Cross hole and keyway | Section geometry and removed volume agree; one intended body |
| Split/ambiguous face | Resolve the intended trimmed region or stop with a clear reason |
| Rebuild after preceding edit | References resolve correctly or fail explicitly |

Do not flip `TURNED_END_FACES_ENABLED` to bypass this matrix. Its current left/right
guard is not enough for vertical-axis turned bases.

## M3: perception to planning

The initial plate/hole/boss pipeline is now implemented in `app/drawing/` and wired
to the browser's **Review and plan** panel. See [DRAWING_PLANNING.md](DRAWING_PLANNING.md)
for the evidence contract, provenance-bound compilation, source corrections,
immutable planning attempts and handoff to the existing approval workflow.
Broader feature recipes and evaluation against engineer-reviewed drawings remain
pending. The following describes the full milestone beyond that initial scope.

Introduce a versioned Drawing Model before changing the build-list contract:
source-page/view callouts, units, projection, dimension provenance, algebraic
derivations, relationships, recognized geometry, conflicts, and unknowns.
Reconcile views, then produce a capability-checked plan with dependencies and
semantic references. The existing human approval pauses and approved-list hash
remain mandatory. A repair that changes strategy requires a new plan approval;
the existing parameter-only repair guard must not be relaxed.

Exit: traceable dimensions and supported construction strategies for reviewed
benchmark parts, with targeted questions for unresolved geometry.

## M4: complex geometry

Prioritize from M1: termination and general ribs/edges, true revolve cut,
sweep, shell/draft, loft, then multibody. Sheet metal and assemblies are separate
tracks with their own references, validation, and benchmark cases.

Each feature requires schema, capability limits, planner support, persistent
execution-layer implementation, verification and live combined-feature cases.
The current generated tool wrappers are not evidence of supported execution.
Do not edit generated wrappers by hand or patch disposable `build/execution-src`.
Introduce a maintained execution extension before adding unsupported native
operations; keep third-party source untouched per its repository rules.

## M5: fidelity and recovery

Add intended axis/opening checks, wall thickness, sweep section orientation,
rebuild/feature-health checks, section previews, and whole-part dimensional
checks. Classify reading, planning, reference and execution failures separately.
Keep full rebuild after approved repairs until persistent references and
checkpoint restoration pass live tests.

## Live execution prerequisite

The pinned MCP source and compiled execution server are now present; see
[SOLIDWORKS_BACKEND.md](SOLIDWORKS_BACKEND.md). MCP discovery and graph compatibility
checks pass. A licensed, COM-ready SolidWorks session is still required before
running the M2 live acceptance matrix. Offline tests cannot certify native CAD operations.
