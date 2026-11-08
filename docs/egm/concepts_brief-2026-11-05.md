# Drawing-to-SolidWorks application improvements

* **Author:** Akash-Chandrasekar24
* **Date:** 2026-11-05
* **Status:** Six independent Goldfish brainstorm reviews completed. The author later authorized evaluation implementation and live testing; production quality/performance acceptance remains pending.
* **Workflow:** EGM brainstorm.
* **Immutable review target:** [brainstorm-input.md](docs/egm/brainstorm-input.md).

## Overview

Implementation follow-up: the requested R1/R2 OCR-assisted comparison has now
been implemented and exercised. See the [measured results](docs/R3_EXTRACTION_RESULTS.md)
for timings, bounded text-recovery scores, failed attempts and important errors.
The earlier brainstorming findings below remain the historical design context;
they are not claims that the complete application improvement is finished.

The first priority is complete and accurate extraction from engineering images and PDFs. The application should produce a source-linked account of the drawing's dimensions, symbols, views, sections, features and uncertainties, let the user correct it beside the source, and pass reviewed evidence to the SolidWorks worker. CAD then builds an editable model and native drawing sheets, pauses on questions or failed checks, and retains final source-to-model comparison and export confirmation.

The author has refined the target to **complete extraction within 30 seconds for a standard page, with up to 10 additional seconds for a complex page (40 seconds total)**, using the current Domo model at medium extraction effort; CAD retains its required model and high effort. That deadline includes feature interpretation, projection symbols and section evidence. A measurement preview, cached replay or incomplete timeout does not meet it. Whether a different extraction model within Domo may be tried remains unresolved; no provider or model change is authorized by this brief.

All six lenses recommend improving the existing orchestrator and two workers. They identify useful foundations, but do not establish the deadline or complete recognition accuracy. The first recommended step is a bounded, quality-scored feasibility experiment on manually inventoried detailed drawings. Recommendations below are reviewer synthesis, not author approvals or rejections.

### Author refinement after Goldfish review

The author permits one extension of up to 10 seconds for complex pages. The total
budget is 40 seconds, not a further 40 seconds after the first 30. Extraction
remains medium effort and CAD remains high. This timing refinement follows the
six reviews; the immutable review input and raw reports retain their original
30-second hypothesis.

Define and record complexity criteria before benchmarking, using source content
such as dense callouts, multiple sections or component/BOM detail. Thresholds
remain to be agreed; slow processing alone must not relabel a standard page as
complex. Classification must not add an unmeasured model pass outside the clock.

Proposed timeout behavior: show that the complex page is using its additional
time, preserve partial evidence if still incomplete at 40 seconds, and expose a
clear incomplete/review outcome. Late or partial results cannot count as passing
the extraction target or automatically advance to CAD. An explicit retry or
further processing policy still needs design. No timeout behavior is implemented
by this documentation update.

## Analysis

### Author clarification during technical drafting

The OCR-assisted design must preserve and inspect each drawing-view image,
not only OCR text. The extraction agent cross-checks the original whole sheet
and actual source crops against located readings, identifies missing dimensions
or views, and connects shape/section evidence with measurements. Required
discrepancies remain explicit review items before CAD acceptance. The technical
draft defines this requirement; OCR integration and its performance are not yet
implemented or validated. This clarification follows the six Goldfish reviews.

The author subsequently agreed to proceed with this hybrid flow and requested
an audit of other extraction gaps. The [gap audit](docs/egm/extraction-gap-audit.md)
records current code limitations and small offline reproductions, including
repeated-callout undercounting and differing tolerances inheriting the same
merged row text. These findings add acceptance requirements; they are not
production fixes or a new measured accuracy result.

### Blocker: a performance commitment is unsupported

The recorded medium-effort detailed-page run took **98.8 seconds for measurements plus 65.7 seconds for feature interpretation: 164.5 seconds**, about 5.5 times the target. It returned seven view boxes and 59 dimension observations, which are counts rather than accuracy evidence. A separate four-drawing run took 244.4 seconds until measurement review and 341.2 seconds combined, observed by polling at 342.8 seconds. These are historical observations, not current latency distributions or guarantees. See [EXTRACTION_PERFORMANCE.md](docs/EXTRACTION_PERFORMANCE.md).

The current agent-region path must obtain the whole-page reading before it has crop boundaries; packed crop calls follow, with extra calls when several sheets are required. Feature interpretation then consumes the reconciled readings. These dependencies are explicit in `app/perception/extract.py:237–314` and `app/drawing/planner.py:178–210`. Two-page concurrency and a process-wide Domo limit of two improve throughput, but do not remove this one-page critical path. Better progress reporting also cannot supply the missing speed.

A consolidated reading/interpretation experiment might reduce dependent calls; independently supplied boundaries might permit parallel reads. Both need a direct quality comparison, especially for small callouts and internal sections. Neither is demonstrated to meet the 30-second standard or 40-second complex-page target. If no allowed variant meets the jointly agreed speed and quality criteria, report the measured limitation and return the target/model tradeoff to the author.

### Blocker for complete evidence: matching values do not identify printed occurrences

Existing readings preserve raw strings, page/crop evidence and human corrections. However, `combine` groups by normalized token and associates every crop containing that token; reconciliation gathers matching readings the same way (`app/perception/extract.py:147–169`; `app/drawing/model.py:188–201`). Repeated equal values can share ambiguous attribution. The normalization strips quantity prefixes and tolerances for matching and scoring; **raw text is still retained** (`app/perception/dims.py:13–27`). Therefore a correct nominal token cannot establish correct quantity, tolerance, attachment or physical-feature identity.

The recommended extension is an occurrence record within the existing evidence model: immutable extraction revision, source page and region or equivalent locator, original text/symbol appearance, interpreted meaning, related view/section/feature, and uncertainty. Retain separate printed, derived and unsupported information. Preserve unrecognized symbols rather than coercing them into lengths. When exact localization is unavailable, show the ambiguity and allow a source-linked human correction.

Feature interpretation currently shares the compiler's `plate | hole | boss | unsupported` vocabulary (`app/drawing/planner.py:26–39`). Recognition needs a richer vocabulary independent of build support: a recognized thread, cavity or shoulder must survive as evidence even when no supported recipe can construct it. A small capability mapping can retain existing unsupported-feature blockers without adding another agent or evidence platform.

### Major: complete processing, complete evidence and approval are different results

The browser saves measurements before background feature/BOM processing. Its background `extraction_status="complete"` means processing returned without an exception; observations may still contain questions (`app/web/server.py:168–206`). Missing crop replies can produce warnings and default views (`app/perception/extract.py:274–288`). Successful dispatch and model agreement are insufficient evidence that every source detail was recognized.

The experience should expose preview, active extraction, processing finished with unresolved evidence, reviewed evidence, plan approval, geometry verification and export confirmation. Show required categories still pending. Preserve distinct failed, interrupted, unreadable, conflicting and unsupported outcomes. A recognized but unsupported feature is not a reading error; another confirmation click cannot make its CAD capability exist.

Questions should locate the source, explain the decision needed and offer an appropriate action: correct a reading, add omitted evidence, supply missing information, retry extraction, or address unsupported construction. Group related questions and retain resolved answers. Users should not repeatedly confirm correct fields. The current model selector also needs clear scope: both workers share `DOMO_MODEL`, while effort is role-specific (`app/agent/workers.py:24–38,48–63`; [AGENT_WORKFLOW.md](docs/AGENT_WORKFLOW.md)). Preserve existing explicit overrides and saved-job behavior.

### Major: CAD provenance needs actual geometric checks

The browser compiler supports a rectangular plate, plain holes and circular bosses; other observations block readiness (`app/drawing/planner.py:140–174,213–312`). This is narrower than the existing execution menu and separate assembly workflow. Section recognition does not expand construction coverage.

Current parameter bindings, derivation reasons and provenance are useful. Native step verification already reads geometry and mass properties before and after operations and checks document identity, body counts and expected volume changes (`app/verify/step_verifier.py:92–159`). Reuse these mechanisms. They do not alone establish sketch constraints, every dimensional tolerance, hole location, wall thickness or internal shape. The development notes report outstanding engineer-reviewed references and live CAD acceptance; [cad-coverage.md](docs/cad-coverage.md) records zero verified drawings in its baseline.

Each supported operation should connect a concise engineering purpose and approved source constraints to generated sketch entities/features and specific native checks. Every line, arc and constraint must contribute to a required profile, location or relationship; construction lines and axes must remain distinct from physical edges. Deterministic recipes can provide this mapping for grouped operations, without a model call for every primitive.

**Illustrative contract, not a drawing reading or completed native test:** suppose reviewed section A–A supplies a blind hole's `Ø10 ±0.05` and depth `12 mm`, while an external view supplies its position.

| Evidence and intent | Sketch/feature operation | Required native result |
| --- | --- | --- |
| Source occurrences, section/cutting-plane identity, units and reviewed location are linked to one hole. Intent: create the specified blind bore while preserving material beneath it. | A supported recipe places the circular profile on the approved face, constrains its centre and nominal diameter, and makes a bounded blind cut. Reference axes are marked as construction geometry. | Read back diameter, centre, axis and blind depth; compare them with approved constraints. Check the intended material removal, remaining material and body count. Record expected/actual values and method. |
| The printed manufacturing tolerance is retained as `±0.05 mm`. | The approved CAD nominal remains `10 mm`; any alternate modeled value needs an explicit decision. | A separately defined numerical comparison tolerance handles API/kernel precision when checking the modeled nominal. It must not be silently substituted for, or derived from, the manufacturing tolerance. |

Before accepting geometry, check units, required profile closure, constraint consistency, position and intended dimensions. Missing measurement support must be recorded as **not checked**, with acceptance blocked where that check is required. Unexpected geometry, an unsupported operation or unresolved evidence pauses the build with a specific question. Record engineering intent rather than private model reasoning. Licensed SolidWorks acceptance remains necessary for new checks and capabilities.

### Major: reuse and compatibility determine whether improvements are safe

Background enrichment already interprets features, while `Orchestrator.plan` interprets the reviewed model again (`app/web/server.py:183–200`; `app/agent/orchestrator.py:113–119`). Human corrections can make that second pass necessary. Reuse only an interpretation whose source revision, reviewed-model digest, prompt/schema version and model/effort match; otherwise invalidate it and reinterpret. This can reduce downstream repetition but cannot prove the initial new-drawing deadline through cached results.

`DrawingModel` fixes version `"1"` and forbids extra fields; correction IDs include extraction ordering and issue-list positions (`app/drawing/model.py:19–20,101–111,157–214`). Richer evidence needs a versioned transition, migration readers or a bounded sidecar. Keep old readings and approved jobs usable, but mark missing legacy evidence as **not recorded**, never verified absence. Re-extraction creates a new revision; transfer corrections only with explicit source mapping and confirmation. Changed evidence or geometry invalidates dependent approval. Pin in-flight work to its selected implementation during rollback.

For the first experiment, extend existing JSON artifacts and traces with one extraction/result ID, per-stage/call/attempt correlation, queue versus provider time, complete-result availability, quality outcomes, requested effort and cache status. Link source constraints and approval hashes to native check results. Record calls/tokens, including available reasoning usage, and failures together with latency. This is enough to diagnose the experiment; a distributed monitoring service is not required.

Domo capacity is shared, and background execution is process-local. Start with bounded pages/pixels, queued work and per-drawing call budgets. Add durable execution or shared admission coordination only if restart guarantees or multiple application processes enter scope. Actual account quotas, billing and enforceable cost limits remain unverified.

## Goals vs. Non-Goals

### Goals

* Preserve printed dimensions, units, tolerances, quantities, material, relevant notes, title-block data and component/BOM readings where present, without substituting guessed numbers.
* Read diameter, radius, angle, depth, thread, countersink and counterbore callouts; preserve GD&T frames, datums, surface-finish and welding symbols, including uninterpreted symbols.
* Identify views, orientation, projection symbols, section labels and cutting-plane references. Use sections and external views together for bores, cavities, shoulders, blind depths and wall thicknesses. Hatching denotes cut material and must not become invented ribs or grooves.
* Attach observations and corrections to source evidence. Distinguish repeated occurrences, printed and derived measurements, inconsistent readings and unsupported interpretations. Carry human corrections into CAD.
* Show source evidence beside reviewable numeric and nonnumeric records; expose omissions, unreadable details, conflicts and unavailable capabilities with targeted recovery actions.
* Evaluate completeness and correctness against manually checked inventories of representative detailed inputs, including small symbols and internal sections. Score recognition, supported interpretation and build capability separately; model agreement is supplementary.
* Pursue complete single-page extraction within 30 seconds for standard pages and within 40 seconds for complex pages, under the current Domo model and medium effort. Agree the complexity criteria, corpus, completion boundary and timing percentile before evaluation.
* Keep CAD at high effort and build only from reviewed evidence. Give each sketch entity, constraint and feature a concise purpose and measurable requirements; use deterministic grouping and actual native geometry checks.
* Preserve existing page rendering, extraction paths, saved results, legacy feature-list jobs, broader feature recipes, assembly behavior, native sheets, repair limits, execution locking, human approvals, final comparison and export confirmation.

### Non-Goals

* Promise perfect automatic recognition, a 30/40-second guarantee, or measured native CAD fidelity from this brainstorm.
* Substitute previews, cached replay, counts, model confidence or nominal-token recall for complete extraction acceptance.
* Change provider/model or reduce required effort through this brief. Whether extraction may change models within Domo remains open.
* Silently infer missing dimensions or projection from layout/country, merge equal numbers into one feature, drop unsupported details, or build a partial supported subset as if it were complete.
* Add agents, a general evidence graph or distributed infrastructure before a demonstrated need; require one model call for each line.
* Enable arbitrary geometry or broader assembly capabilities through recognition changes alone, or remove existing review gates.

## Alternatives Weighed

These are reviewer recommendations for discussion; **the author has not rejected a strategy or approved an implementation**.

| Alternative | Benefit and principal objection | Recommended disposition |
| --- | --- | --- |
| Keep current independent page/crop reads and feature interpretation | Preserves an existing comparison baseline; recorded detailed-page latency is far above target and the two reads can share omissions. | Retain as control and fallback. Measure full evidence quality. |
| Consolidate reading and feature interpretation into fewer structured calls | Attacks dependent call count directly; risks losing independent checks, fine detail and source identity. | First bounded candidate experiment at the same current Domo model and medium extraction effort. |
| Parallel whole-page and crop reads using independently available boundaries | Could shorten the critical path; current agent-generated boundaries depend on the whole-page reply, and boundary generation can add cost or miss views. | Compare only where valid boundaries already exist or their full generation cost is included. Preserve current boundary behavior until validated. |
| Coarse inventory followed by targeted detail crops | Preserves local resolution for small symbols; an omitted inventory item may never trigger a crop, and extra calls consume the latency budget. | Candidate for failures revealed by the checked inventory, with bounded work and explicit unreadability outcomes. |
| Reuse matching feature interpretations after review | Removes redundant downstream inference; stale evidence after human corrections would compromise CAD. | Reuse only with dependency validation and invalidation. Do not count cached reuse as initial extraction performance. |
| Change extraction model, add agents, or broadly expand CAD now | Could alter capability or speed; model permission, quality, costs and representative geometry needs remain unresolved. | Model change requires a separate author decision. No evidence currently requires additional agents; rank CAD additions from checked cases. |

## Key Decisions & Staging for Next Steps

### Author decision during drafting: S2 evidence sidecar

The author selected S2: keep the existing drawing result and add versioned,
linked evidence artifacts for richer engineering readings and corrections.
The draft develops immutable revisions, a derived compatibility model and
evidence-bound approvals. S1 remains unselected; no unstated author rationale
for rejecting it is inferred. This decision follows the original six reviews
and does not approve implementation or establish extraction performance.

### Fixed requirements and proposed direction

The author also selected R3 during drafting: compare a joint visual/OCR
assessment (R1) with a separate visual-verification sequence (R2) before choosing
the production default. Use the same model, medium extraction effort and checked
inputs, with S2 evidence revisions. The [comparison protocol](docs/egm/extraction-comparison.md)
records the proposed trial controls and prerequisites. No comparison results are
available yet; CAD remains high effort.

Domo/current model, extraction medium, CAD high, full source-backed evidence, existing functionality and human gates remain requirements. The proposed direction is to extend the current evidence/handoff/verification contracts and test fewer dependent extraction calls. This is not an approved implementation plan.

### 1. Agree the first feasibility experiment

Select the exact detailed page and a small representative set with readable small symbols, repeated callouts, internal sections and relevant component/BOM content. Include the historical triple-clamp case if suitable; an easy plate alone cannot validate the intended detailed-page or assembly use. Have an engineer inventory the full callouts, symbols, view/projection/section links, features, notes and unresolved source details. Existing benchmark truth and CAD references still need review.

Agree quality thresholds by category, allowable human correction burden, latency percentile, repetition count, concurrency/load and cost ceiling before comparing variants. Define start and finish precisely, including whether upload transit counts. Measure from the agreed start through ingest, queue wait, page/crop reads, feature and applicable BOM stages, validation and persisted/available complete result. Keep preview latency and subsequent human review/CAD time separate. A timed-out incomplete result is a failure, not a fast success.

### 2. Run a controlled extraction comparison after authorization

Use the existing pipeline as control and one consolidated structured extraction candidate on the same sources, Domo model and medium effort. Use uncached model responses for new-drawing performance. Preserve originals and actual transmitted resolution/transform metadata. Compare complete inventory coverage, wrong readings, wrong interpretations, source associations, unresolved questions, calls/tokens and full latency together.

**Decision gate:** adopt a candidate only if it meets the agreed quality and timing contract. If it is faster but omits required details, it fails. Score standard pages against 30 seconds and pre-classified complex pages against 40 seconds. If quality improves but exceeds the applicable limit, record that result and ask the author to decide the unresolved target/model tradeoff. No such experiment was run during this brainstorm.

### 3. Make reviewed evidence reusable and compatible

Define stable occurrence/question identity and structured nonnumeric evidence within an explicitly versioned contract. Extend existing readings and correction layers rather than introducing parallel mutable truth. Add explicit stage/coverage/review states and targeted questions. Preserve old API/progress clients and legacy artifacts through readers/adapters; missing old evidence stays visible. Validate correction transfer, approval invalidation and rollback behavior before changing the default path.

### 4. Prove the downstream case in licensed SolidWorks

Choose the exact editable model and native sheet outputs, source dimensions, internal checks and approved references for the first end-to-end case. Map engineering intent and constraints to deterministic recipes and actual measurements, including section-revealed geometry. Preserve existing volume/body/document checks. Add only the capability required by this representative case, with live combined-feature acceptance. Keep source review, plan approval, parameter-only repair safeguards, uncertain-execution reconciliation, final comparison and export confirmation.

### Unresolved author choices

* Which exact drawing/part defines the first successful end-to-end case, and which additional pages make extraction acceptance representative?
* Which dimensions, internal features, tolerances, editable model and native drawing outputs must it satisfy?
* What quality thresholds, acceptable human correction burden and handling of unreadable source material define success?
* Are readable source details, engineer-checked inventories and reference CAD models available?
* What complexity criteria, start/finish definition, latency percentile, repetition count, load and cold-cache policy apply to the 30/40-second targets?
* What call/token or monetary cost limit and Domo account quotas apply?
* May the extraction model change within Domo if fixed-model experiments miss the target? The CAD model/high-effort requirement remains.
* What does model selection control, given the current shared model setting and preserved explicit overrides?
* Which broader assembly capabilities belong in the first release, and which supported CAD checks/API extensions are needed for the chosen case?

### Evidence and reviewer status

All six fresh-context Goldfish actually completed on attempt 1. Each returned an explicit attestation that its full persona, immutable target and all seven selected evidence files were read. Separate standards were **not required in BRAINSTORM** by these personas; REVIEW-only standards, including the zoom-out standard, were not applied. The synthesizer read all six actual persona files and the plugin's `agents/synthesizer.md` and `docs/codex.md` in full.

| Reviewer | BRAINSTORM lens | Status | Full reads attested | Raw report |
| --- | --- | --- | --- | --- |
| design_critic | Architecture, capacity and economics | completed | Persona, target, seven evidence files; standards none | [design_critic.md](docs/egm/reviews/design_critic.md) |
| codebase_details | Observability and operational diagnosis | completed | Persona, target, seven evidence files; standards none | [codebase_details.md](docs/egm/reviews/codebase_details.md) |
| zoom_out | Simplicity and reuse | completed | Persona, target, seven evidence files; standards none | [zoom_out.md](docs/egm/reviews/zoom_out.md) |
| comprehension | UX and ergonomics | completed | Persona, target, seven evidence files; standards none | [comprehension.md](docs/egm/reviews/comprehension.md) |
| implementation_readiness | Migration, compatibility and rollout | completed | Persona, target, seven evidence files; standards none | [implementation_readiness.md](docs/egm/reviews/implementation_readiness.md) |
| structural | Coupling and maintainability | completed | Persona, target, seven evidence files; standards none | [structural.md](docs/egm/reviews/structural.md) |

The same selected evidence list went to every reviewer: `docs/EXTRACTION_PERFORMANCE.md`, `docs/AGENT_WORKFLOW.md`, `app/agent/orchestrator.py`, `app/agent/workers.py`, `app/perception/extract.py`, `app/drawing/model.py`, and `app/drawing/planner.py`. Reports retain additional inspected code and specific line references. Plugin personas resolved under `C:\Users\AkashChandrasekar\.codex\plugins\cache\egm\egm\0.1.0\agents\`; each raw report records its exact absolute persona path and read attestation.

Reviewers were launched with native `fork_turns: "none"`, at most two concurrently, without parent transcript or cross-review findings. Fresh conversation isolation does not independently establish exclusion of host instruction files; no such exclusion probe was performed. No reviewer retrieved conversation history or memory. Goldfish remained read-only; the synthesizer saved their raw reports and this brief.

**Limits of this result:** static brainstorming and existing recorded measurements only. No new provider calls, accuracy benchmarks, load/cost measurements or native CAD execution were performed. Feasibility of the 30/40-second targets, complete recognition quality, account economics and actual geometric acceptance remain unestablished. Review completion is not implementation approval.

