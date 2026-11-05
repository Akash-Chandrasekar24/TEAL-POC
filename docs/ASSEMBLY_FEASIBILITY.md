# Complex assemblies: what will work in SolidWorks (researched 2026-11-05)

Sources: the pinned upstream execution layer source (`build/execution-src/...`, written against a licensed SolidWorks), the SolidWorks API
help (its pages are JavaScript-rendered, so only search snippets were readable), and this repo's code. **Nothing below was run in SolidWorks.**

| Capability | Verdict | Basis |
|---|---|---|
| Sub-assemblies flattened to leaf parts (`app/assembly/compose.py`) | Will work | Upstream inserts leaf parts only (`AddComponent5` after opening the file as a part) and records nested assemblies as a gap, so flattening is the supported path. The saved assembly has one level, not the BOM hierarchy. |
| Linear / circular patterns as explicit instances | Will work | Same insert + mate tools. Native *component* patterns are not exposed by upstream (it has feature patterns only: `FeatureLinearPattern3`, `FeatureCircularPattern4`). |
| Rotated placement composition | Likely, **confirm live** | `ArrayData` rotation rows are the transformed axes (row-vector, `p' = p·M + t`). Upstream recorded this as "proven empirically" for a sketch transform; help diagram agrees. Not yet shown for a component `Transform2`. |
| The 8 mate types | Works (upstream maps exactly these) | `coincident, concentric, perpendicular, parallel, tangent, distance, angle, lock`. |
| Edge mates | Cheap to add | Upstream `add_mate` already takes `a_kind/b_kind` = `face` or `edge` with indices from `analyze_assembly(edges)`. Our plan model only has plane/cylinder faces. |
| Other mate types (width, symmetric, gear, hinge, limits) | Needs our own C# extension | Upstream rejects any other name. `AddMate5` is marked obsolete (replacement `IAssemblyDoc.CreateMate`); it still exists, but a new extension should target `CreateMate`. The full `swMateType_e` list could not be read. |
| Per-step verification of every component | Works, but O(n²) | `analyze_assembly components` has no per-component filter, so each step re-reads all components. Fix in Python by batching, or add an endpoint to our extension. |
| Constraint status, interference | Works with caveats | `IComponent2.GetConstrainedStatus` reportedly reports only first-level components (secondhand forum report); interference needs `TreatSubAssembliesAsComponents` set deliberately. Moot while we stay flat. |
| Insertion speed | Slow per part | Upstream opens and closes each part file for every insertion, even a repeated part. `ForceRebuild3(TopOnly)` and deferring rebuilds are the documented levers. |

## Live acceptance checklist (run once the licence is in)
1. **Transform convention:** insert an asymmetric part at a 90 deg rotation about z with translation (3,-4,7); read back a known face point. Expect the row convention (`x` -> `+y`). If not, flip `_row_apply`/`_axis_angle` in `compose.py`.
2. **Composed instance:** a rotated sub-assembly instance; compare every component's bounds to the composed prediction.
3. **Bolt circle:** 8-copy circular pattern with one mate per copy; all fully constrained, no interference.
4. **Scale:** time a 50-part flat assembly; record readback call counts before the batching change.
5. **Mate readback:** each of the 8 mate types once; names, alignment and values match the plan.
