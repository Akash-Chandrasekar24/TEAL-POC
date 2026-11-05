# Drawing-to-SolidWorks application improvements

* **Author:** Akash-Chandrasekar24
* **Date:** 2026-11-05
* **Status:** Working hypothesis ready for the author's readiness review. No Goldfish review has run; implementation is not approved.
* **Workflow:** EGM brainstorm.

## Current focus from the author

The first priority is complete and accurate data extraction from 2D drawing
images. CAD construction is a downstream consumer; this brainstorm first needs
to establish what a trustworthy extraction result contains and how to check it.

The following acceptance criteria are proposed for clarification:

* Preserve printed dimensions, units, tolerances, quantities and symbols without
  substituting guessed numbers.
* Identify views, projection symbols, section labels and cutting-plane references,
  and connect feature observations to their supporting views and measurements.
* Preserve relevant notes, materials, title-block information and component/BOM
  readings where they appear.
* Show source evidence beside extracted readings so a person can verify them.
* Expose missing, unreadable, inconsistent and unsupported details for human
  correction. A plausible answer must not count as a verified answer.
* Evaluate completeness and correctness against a manually checked inventory
  for representative inputs. A second model agreeing is not sufficient proof.
* The author's latency target is **under 30 seconds for one detailed drawing
  page**, including feature, projection-symbol and section extraction. A partial
  preview does not satisfy complete extraction. Test-image selection and the
  measurement percentile remain open.

These criteria are working hypotheses, not a promise of perfect automatic
recognition. No Goldfish reviewers have been launched.

### Feasibility constraint

The recorded detailed-page test took 98.8 seconds until measurement review and
another 65.7 seconds for feature interpretation. The proposed deadline is more
than five times faster than that combined result. Removing UI delays or
processing different pages concurrently cannot by itself meet this single-page
deadline. Combining model passes or changing the extraction model needs an
accuracy comparison, particularly for small callouts and internal sections.

Domo remains the selected provider. Whether the extraction model may change
within Domo is unresolved; the CAD model and high effort remain requirements.
No model change is authorized or implemented by these brainstorming notes.

## Anchored working hypothesis

Use the current Domo extraction worker at medium effort to produce a complete,
source-linked reading of a detailed 2D drawing in under 30 seconds, including
symbols, projection, sections, feature observations and uncertainties. The
SolidWorks worker stays at high effort and builds only from reviewed evidence,
with a short engineering purpose and measurable constraints for its geometry.
An unresolved reading or failed geometry check pauses the workflow for correction;
the orchestrator must not describe incomplete or unverified work as complete.

### Extraction must demonstrate engineering understanding

* Read diameter, radius, angular, depth, quantity, thread, countersink and
  counterbore callouts; preserve GD&T frames, datums, surface-finish and welding
  symbols where present. Preserve symbols that cannot yet be interpreted and
  ask for clarification instead of silently dropping them.
* Identify projection symbols, view orientation, section names and cutting-plane
  references. Use section and external views together to describe internal bores,
  cavities, shoulders, blind depths and wall thicknesses. Hatching represents
  cut material and must not become invented ribs or grooves.
* Distinguish printed measurements from derived dimensions and visual guesses.
  Retain tolerances, units, feature counts and source references; equal numbers
  in different views do not establish that they are the same physical feature.
* Check for inconsistent readings across views and sections. Missing source
  details require questions; human corrections must remain attached to their
  source and reach the CAD worker.
* Readability and accuracy must be checked against a human-reviewed inventory of
  representative drawings, including small symbols and internal section details.
  Recognition, supported interpretation and build capability are separate results.

### CAD must justify and verify its geometry

* Each planned line, arc, sketch constraint and feature needs an engineering
  purpose, source evidence and the dimensions or relationships it must satisfy.
  Record a short explanation of intent rather than private model reasoning.
* Distinguish construction lines, axes and reference geometry from the edges
  that define a physical profile. A line should contribute to a required profile,
  location or constraint; duplicated or unrelated geometry should be rejected.
* Validate units, profile closure where required, constraints, position and
  intended dimensions before accepting a sketch or feature. Measure resulting
  geometry in SolidWorks and compare it with the approved constraints and
  tolerances, including internal geometry revealed by sections.
* Compare the intended change with the actual change. An unexpected measurement,
  unsupported operation or unresolved interpretation pauses the build and asks
  a specific question; high effort alone is not proof of geometric correctness.
* Group sketch operations and use deterministic checks where possible. Calling
  the model separately for every line is not a proposed requirement and could
  make execution slow and inconsistent.
* Keep the user's final source-to-model comparison and export confirmation.

### Boundaries for the proposed review

* Extraction remains at medium effort and CAD at high. A provider/model change
  has not been authorized; reviewers should test feasibility with that constraint.
* The 30-second target covers full single-page extraction, not CAD construction.
  A preview, cached replay or a timeout with incomplete readings cannot establish
  that the target was met on a new drawing.
* Existing functionality and human approval boundaries remain requirements.
  Broader geometry support needs separate implementation and live validation.
* The exact acceptance drawings, quality thresholds, timing percentile and cost
  limits still need agreement. Reviewers should report what cannot yet be
  established instead of treating these unknowns as satisfied.

## Problem and intended experience

The application should turn engineering images or PDFs into reviewed SolidWorks
models and native drawing sheets. The user uploads a drawing and chooses a
model, reviews the extraction alongside the source image, corrects uncertain
readings, and lets the CAD worker build. CAD questions pause the build. The user
compares the result against the source before approving export.

The intended division is an orchestrator monitoring an extraction worker and a
SolidWorks worker. Existing functionality and human approval boundaries remain
requirements. Domo is the selected provider; extraction uses medium reasoning
effort and CAD uses high.

## Evidence from the current repository

| Finding | Evidence | Implication |
| --- | --- | --- |
| The browser drawing compiler supports rectangular plates, plain holes and circular bosses. Other feature observations remain unsupported. | `app/drawing/planner.py`: `VISION_PROMPT`, `PLAN_PROMPT`, `compile_plan` | Recognizing internal geometry or a complex assembly is different from being able to construct it. The broader execution menu must not be confused with compiler coverage. |
| Feature observations already pass from extraction to the SolidWorks planner. | `app/agent/orchestrator.py`: `Orchestrator.plan` | Improve evidence quality and feature coverage within this handoff before proposing more agents. |
| Projection symbols and section evidence are now included in extraction and review. | `app/perception/extract.py`: `PAGE_PROMPT`, `CROPS_PROMPT`, `read_page`; `app/drawing/model.py`: `projection_reading`, `reconcile` | Recognition quality still needs evaluation on readable, checked examples. Prompt and handoff tests alone cannot prove accuracy. |
| Four different pages took 244.4 seconds until measurement review and about 343 seconds until background completion in a recorded test. | `docs/EXTRACTION_PERFORMANCE.md` | Concurrency improved delivery, but complete understanding still exceeds five minutes for that set. These historical observations are not a current latency guarantee. |
| Reference assembly accuracy remains unvalidated in the development notes. | `docs/CAD_DEVELOPMENT.md`; `docs/cad-coverage.md` | Acceptance needs actual licensed SolidWorks builds and comparison against checked dimensions or reference models. |

## Working hypotheses to challenge

1. **Choose a bounded end-to-end acceptance case first.** Prove that a specific
   drawing can become a verified editable model and native sheet. The strongest
   objection is that one simple part could hide the failures in the user's
   intended assembly drawings; the chosen case must represent the intended use.
2. **Treat extracted information as evidence.** Connect each feature, section,
   projection reading and parameter to its source and human correction. The
   objection is added review effort: a user should answer targeted questions
   rather than confirm every correct field repeatedly.
3. **Measure speed together with correctness.** Evaluate time to first useful
   review, time to complete extraction, provider calls and missed or incorrect
   readings. Combining passes or lowering effort further could reduce latency
   while losing the independent checks that catch omissions.
4. **Expand the compiler in small verified increments.** Add operations needed
   by the selected case and verify their geometry in SolidWorks. The objection
   is slower apparent feature growth, but exposing unverified operations would
   move failures into the user's final review.

## Questions still open

* Which exact drawing or part defines the first successful end-to-end result?
* Which dimensions, internal features and native outputs must that result satisfy?
* Can the extraction model change within Domo, and what level of human correction
  is acceptable for that case?
* Are readable source details or checked CAD references available for evaluation?
* Which broader assembly capabilities belong in the first release?

These are open items for the proposed feasibility review. No strategy has been
rejected by the author and no implementation plan is agreed yet.

## Next step

Ask the author whether this anchored hypothesis is ready for independent
Goldfish review or needs more clarification. Start reviewers only after explicit
readiness confirmation. Their findings will replace the preliminary hypotheses
with a reviewed analysis, including the feasibility of the 30-second target.
