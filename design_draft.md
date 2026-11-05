# Engineering drawing extraction and verified SolidWorks construction

* **Author:** Akash-Chandrasekar24
* **Status:** Evaluation implementation authorized and tested; broader design
  and production acceptance remain in progress.
* **Date:** 2026-11-05
* **Predecessor:** [Concepts brief](concepts_brief.md)
* **Drafting progress:** The author approved Overview & Goals. The Technical Plan
  below develops the agreed OCR-assisted visual cross-check. OCR tooling,
  the R1/R2 comparison protocol, OCR tooling and timing/recovery details remain
  to be finalized. Storage uses S2; R3 will compare both model-call arrangements.

## Implementation follow-up

The author subsequently explicitly requested implementation and accuracy tests,
ending the design-only workflow for that work. OCR-assisted R1/R2 evaluation
paths, local Windows OCR, immutable S2 trial evidence and view artifacts, and a
live comparison runner are now implemented. The upload selector exposes the
trials; the existing reviewed extraction remains the default.

See [measured results and limits](docs/R3_EXTRACTION_RESULTS.md). Three sheets
were exercised with uncached Domo calls. R1 was faster in these observations;
both paths still misread important rotated callouts on the dense source. Full
30/40-second performance and engineering accuracy acceptance were not met.
The offline suite passed 223 tests plus UI checks.

This is an extraction-evaluation implementation. S2 trial evidence remains
pending human/engineering acceptance and is blocked from native CAD start/export.
The full correction-to-CAD S2 approval contract described later is not yet a
production capability. Native PDF text reading, broader symbol semantics,
calibrated complexity/timing policies, and representative engineer-verified
accuracy acceptance also remain outstanding. Subsequent sections retain the
proposed broader architecture and its open choices rather than claiming that
every proposal has been implemented.

## Overview

Improve the existing drawing-to-SolidWorks application so it preserves the
engineering meaning of an uploaded image or PDF and constructs geometry from
reviewed evidence. The orchestrator continues to coordinate the extraction and
SolidWorks workers. Extraction uses the selected Domo model at medium effort;
CAD uses high effort.

The user needs an extraction result that accounts for dimensions, tolerances,
quantities, important symbols, projection, section names, cutting planes and
features. Each reading must identify its source so the user can correct
uncertainties alongside the drawing. A recognized feature must remain visible
even when the current CAD compiler cannot build it.

The CAD worker needs an engineering purpose and measurable requirements for
each generated sketch entity, constraint and feature. It must compare the
resulting native geometry with the approved requirements, pause when it cannot
justify or verify an operation, and preserve final user comparison and export
confirmation. Higher reasoning effort supplements these checks; it does not
replace them.

The performance targets are complete extraction within **30 seconds for a
standard page** and **40 seconds total for a complex page**, including its
permitted 10-second extension. These are targets to validate through experiments,
not established application performance. Existing functions, saved work and
approval boundaries remain requirements.

## The Problem

The current workflow reads the whole page, reads packed view crops and then
interprets features. On the recorded detailed-page test, measurement reading
took 98.8 seconds and subsequent feature interpretation took 65.7 seconds. That
164.5-second result is substantially above either new target. Page concurrency
and early previews help users start reviewing, but do not remove the dependent
work required to finish one page. See the [recorded measurements](docs/EXTRACTION_PERFORMANCE.md).

The evidence representation also limits engineering interpretation. Raw callout
text is retained, but normalized tokens used for matching remove tolerances and
some quantity prefixes. Matching identical values across views does not identify
which printed occurrence belongs to which physical feature. Important symbolic
meaning must survive extraction, review and transfer to CAD independently of
nominal numeric matching.

Processing completion does not establish complete or accurate understanding.
A returned feature list can still contain questions, missing evidence or
unsupported geometry. Users need clear distinctions between an active read,
an incomplete or uncertain result, reviewed evidence and a verified CAD result.

The browser compiler currently supports a rectangular plate, plain holes and
circular bosses. Other execution paths have broader capabilities, but those do
not establish arbitrary drawing reconstruction. Existing native checks provide
useful volume, body and document measurements; they do not by themselves prove
every dimension, internal shape or constraint. Additional supported checks need
actual SolidWorks acceptance evidence.

## Goals

1. **Preserve the drawing's engineering content.** Capture printed dimensions,
   units, tolerances, quantities, relevant notes, materials, title-block and
   component/BOM content. Preserve diameter, radius, angle, depth, thread,
   countersink, counterbore, GD&T, datum, surface-finish and welding information
   where present. Uninterpreted symbols remain source-linked review items.
2. **Read views and sections together.** Preserve projection-symbol evidence,
   section names and cutting-plane references. Connect section and external
   views to internal bores, cavities, shoulders, blind depths and wall
   thicknesses. Do not turn hatching into invented physical geometry.
3. **Make readings and corrections traceable.** Distinguish repeated printed
   occurrences, printed values and derived dimensions. Each observation and
   correction must locate its source and identify its relationship to views,
   sections and physical features. Correction of one occurrence must not
   silently change other equal-valued callouts.
4. **Expose uncertainty and capability limits.** Preserve missing, unreadable,
   conflicting and unsupported information as explicit outcomes. Let the user
   correct numeric and nonnumeric readings, supply omitted evidence and resolve
   targeted questions. Recognition and CAD build support are separate results.
5. **Validate complete extraction against the timing targets.** Evaluate new
   standard pages against 30 seconds and complex pages against 40 seconds total.
   Account for ingestion, queuing, model calls, reconciliation, required feature
   and BOM interpretation, validation and result availability. Agree the precise
   clock boundary and complexity rules before benchmarking. A partial preview,
   cache hit or incomplete timeout cannot establish new-page performance.
6. **Measure quality alongside latency.** Use engineer-checked inventories of
   representative drawings to evaluate omissions, incorrect readings,
   interpretation errors, source associations and human correction burden.
   Required information must not be silently omitted or guessed to pass a timing
   test. Agree category thresholds before accepting a candidate extraction path.
7. **Build geometry with a checkable purpose.** At high effort, CAD planning must
   connect each operation to reviewed engineering intent, source constraints and
   required measurements. Distinguish construction geometry from physical
   profiles. Grouped deterministic operations may provide entity-level intent;
   a separate model request for each line is not required.
8. **Verify actual native results.** Check the applicable dimensions, positions,
   axes, depths, profile closure, constraints and material changes for supported
   operations. Retain existing native checks. Report expected and actual values,
   units, check coverage and numerical comparison tolerances separately from
   printed manufacturing tolerances. Required checks that cannot be performed
   must remain visibly unverified and block acceptance.
9. **Preserve review and compatibility.** Keep extraction corrections, planning
   approval, CAD questions, repair safeguards, execution locking, final
   source-to-model comparison and export confirmation. Preserve existing saved
   results, legacy jobs, feature recipes, assembly behavior and native sheets.
   Changed evidence or geometry must invalidate dependent approval.

## Non-Goals

* Claim perfect recognition, proven 30/40-second performance or native geometry
  accuracy from prompts, model agreement, counts or this document.
* Change the selected provider/model or reduce the required reasoning efforts
  without a separate author decision.
* Treat unsupported construction as a missing measurement, discard recognized
  geometry, or present a partial supported model as the complete design.
* Add workers, replace agent-generated view boundaries or introduce distributed
  infrastructure as an assumed solution before evidence establishes a need.
* Enable arbitrary part or assembly reconstruction through extraction changes
  alone, or bypass user review to satisfy the timing targets.
* Prescribe a particular extraction architecture, schema migration, timeout
  mechanism or model-selection behavior before the Technical Plan is reviewed.

## Open Questions

* **Acceptance examples:** Which exact detailed images or PDF pages and checked
  source inventories will establish extraction quality and the first native CAD
  acceptance case?
* **Quality criteria:** What category thresholds and human correction burden
  define acceptance, including low-resolution or genuinely unreadable sources?
* **Timing contract:** What complexity criteria, clock boundary, percentile,
  repetition count, load and cold-cache policy define the 30/40-second tests?
* **Incomplete work:** What user actions and recovery behavior apply when a page
  remains incomplete at its deadline? The brief proposes preserving evidence
  and a visible incomplete outcome; the mechanism remains to be designed.
* **Model and cost limits:** If the selected model misses the targets, may another
  extraction model within Domo be evaluated, and what call/token or cost ceiling
  applies? The required extraction/CAD effort levels are already decided.
* **CAD acceptance:** Which native operations, measurement APIs and reference
  parts or assemblies are required for the selected case?
* **Technical choices:** Hybrid extraction and S2 evidence storage are selected.
  OCR tooling, request sequence, timing boundaries, interpretation reuse and
  selector semantics remain open. Unselected alternatives have no attributed
  rejection rationale unless the author supplies one.

## Critical User Journeys

### Review a newly uploaded drawing

* **Given:** An image or PDF contains a readable engineering drawing.
* **When:** The user uploads it and starts extraction.
* **Then:** Processing shows actual stages and elapsed time. The extraction
  result contains source-linked numeric and symbolic observations, view and
  section relationships, feature descriptions, coverage information and
  questions. A preview stays distinguishable from the finished result.

### Correct a reading or an omitted annotation

* **Given:** A drawing has repeated equal dimensions or an ambiguous symbol.
* **When:** The user selects a specific reading or adds missing evidence.
* **Then:** The UI locates its source, accepts an appropriate numeric or symbolic
  correction, and preserves the original reading. Dependent interpretations and
  approvals are invalidated. Other equal-valued occurrences remain unchanged.

### Handle a page that exceeds its budget

* **Given:** A standard page has a 30-second budget, or a page identified as
  complex is eligible for a total of 40 seconds.
* **When:** Processing cannot produce an accepted complete result in time.
* **Then:** The system records a missed target and exposes incomplete evidence
  and remaining questions. No automatic transition to CAD occurs. The exact
  retry, late-result and provider-cancellation behavior remains a design choice.

### Construct and check reviewed geometry

* **Given:** The user has reviewed the evidence and approved a supported plan.
* **When:** The CAD worker builds it at high effort using existing execution
  recipes and the serialized native writer.
* **Then:** Geometry has a source-backed purpose and required checks. The system
  records actual native measurements and pauses on failed or unavailable checks.
  The user compares the model with the drawing before export confirmation.

## Technical Plan (Architecture)

### Scope and component ownership

The plan extends the existing orchestrator and two workers. The following
responsibilities apply to all extraction approaches under consideration:

| Existing area | Responsibility in the proposed design |
| --- | --- |
| `app/ingest/` | Preserve uploaded sources, render PDF pages, identify page dimensions and record transforms between source, model image and displayed image. |
| `app/perception/` | Agent-generated view boundaries and source observations: raw callouts, symbols, sections, notes and component information. Preserve detail and uncertainty. |
| `app/drawing/model.py` | Validate engineering evidence, corrections, units, source references and projection consistency. Evolve through an explicit compatibility contract. |
| `app/agent/workers.py` | Keep extraction at medium effort and SolidWorks planning/repair at high; preserve explicit provider overrides and injected test clients. |
| `app/agent/orchestrator.py` | Control handoffs, budgets, evidence revisions, review pauses and transitions. A worker returning JSON does not establish CAD readiness. |
| `app/drawing/planner.py` | Map reviewed observations to supported feature recipes, retain unsupported geometry and bind parameters to approved evidence. Separate the observation vocabulary from compiler support. |
| `app/assembly/` | Retain component/BOM identity, model binding, assembly review and execution gates. Recognition changes do not imply new assembly reconstruction capability. |
| `app/runner/`, `app/features/`, `app/verify/` | Execute approved recipes, record entity/feature intent, perform supported native measurements and classify failures. |
| `app/web/` | Display the source beside readings and questions; keep previews, complete results, reviews, native checks and export confirmation distinct. |
| `app/trace.py`, `app/llm/traced.py`, `eval/`, `tests/` | Correlate stages/calls and evaluate full latency and quality against checked references. |

This is a responsibility map, not the final implementation-file inventory. That
inventory follows architecture selection in the Detailed Implementation section.

### Shared data flow

```text
Upload -> page ingestion -> ExtractionAgent (medium; approach A/B/C pending)
                                  |
                                  v
                 validated observations + coverage + questions
                                  |
                   source review and human corrections
                                  |
                 reviewed evidence revision and feature bindings
                                  |
                    SolidWorksAgent plan (high)
                                  |
                       explicit plan approval
                                  |
                 serialized native build + measured checks
                                  |
                    final comparison -> export approval

Missing/ambiguous evidence or failed checks -> pause at the relevant stage.
Corrections -> new reviewed revision -> invalidate dependent work and approval.
```

### Shared engineering evidence contract

The contract separates what the drawing shows, what it means and what CAD can
build. Storage uses the selected S2 contract below, with legacy compatibility.

| Record | Required information and behavior |
| --- | --- |
| Extraction revision | Result and attempt identities; original source digest; page identity; schema/prompt version; requested and returned model; effort; extraction approach; image transforms and actual transmitted image identity. |
| Source occurrence | Unique identity scoped to its immutable revision; source page and region or equivalent locator; original text or symbol evidence. Two printed `20` callouts are separate occurrences. IDs must not depend solely on value or sorting order. |
| Engineering interpretation | Category and meaning, including nominal value, units, tolerance form, quantity, datum/symbol relationships and printed-versus-derived origin. Retain raw evidence when interpretation is unavailable. Never force a symbol or angle into a length. |
| View and section | View type, caption, region, projection evidence, section name, cutting-plane reference and supporting observations. Explicitly retain ambiguous parent-view or section relationships. |
| View image artifact | Original-source geometry region and contextual crop region; actual crop image identity and source transform; view/section ID and linked caption/callouts. Preserve pixels separately from text readings and optional display overlays. |
| Physical feature observation | Feature description, supporting occurrences and views, required geometric roles and unresolved questions. Rich recognition categories remain separate from the compiler's supported-operation list. |
| Review decision | Reviewed source and extraction revisions, target occurrence/question, correction or interpretation, author decision and dependency invalidation. Never overwrite immutable source evidence. |
| Coverage report | Categories/regions assessed, observations produced, missing or unreadable declared evidence and validation issues. `Not present`, `unreadable`, `not assessed` and `unsupported` are different outcomes. |
| CAD requirement and check | Source occurrence IDs, reviewed revision, feature/entity purpose, approved constraint, measurement method, expected/actual result, units, numerical comparison tolerance and pass/fail/not-checked outcome. |

Recognized quantities, thread specifications, GD&T and section relationships
must remain structured and reviewable even when construction is unsupported.
Human-added evidence uses the same source-location requirements. Exact schema
types and revision migration details will implement the selected S2 contract.

Validation checks structure, reference integrity, units, incompatible readings,
required roles and declared gaps. It cannot prove that the model saw every mark
in the source. Completeness and interpretation quality must also be measured
against engineer-checked inventories.

### Earlier model-call candidates: retained for experiment design

All three candidates retain the selected Domo model and medium effort, use
agent-generated boundaries, account for the full single-page clock, preserve
human review and keep the current pipeline as the comparison baseline. They are
unranked options; none has demonstrated the required speed and quality.

| Option | Mechanism | Trade-offs | Conditions under which it fails |
| --- | --- | --- | --- |
| **A. Combined page extraction** | One structured model request returns view boundaries, occurrences, symbols, sections, feature observations and applicable component/BOM readings. Deterministic validation follows without another automatic interpretation request. | Fewest dependent model calls and one consistent set of references; loses the current independent page/crop comparison and increases the scope of one response. | Fine detail is unreadable at the transmitted resolution, relationships or categories are omitted, the response is incomplete, or this one request plus validation exceeds 30/40 seconds. |
| **B. Page read plus combined crop interpretation** | First request reads the page and identifies views; the packed crop request reads local evidence and interprets features/sections together. Reconcile the two readings without a separate mandatory feature pass. Additional sheets remain explicit work. | Retains whole-page/local comparison and close-up detail; keeps at least two dependent calls and can require more for dense pages. | Provider latency or crop packing exceeds the budget, the first request omits a view, packed text loses readability, or cross-read disagreement cannot be resolved. |
| **C. Combined page extraction with targeted follow-up** | Start with A; use bounded close-up requests only for specifically uncertain or missing evidence when time remains. All unresolved regions stay visible. | Spends extra work on difficult details; variable call count and more complex scheduling/dependency tracking. | An omitted detail is never identified as needing a crop, follow-up work cannot finish within the budget, or a partial result is incorrectly labelled complete. |

The first experiment must compare the selected candidate with the existing
pipeline on the same checked sources. Selecting a candidate authorizes its
design direction, not a claim of feasibility or a provider/model change.

### Additional accuracy approach: combine independent evidence sources

The author asked whether approaches beyond changing model-call count could
improve extraction and has since agreed to the OCR-assisted visual cross-check.
The earlier A/B/C options describe model-call arrangements; they do not cover
every way to acquire better evidence. The hybrid direction is agreed; specific
dependencies and scheduling still require selection and validation; S2 storage
is now selected.

A source-aware hybrid can let the extraction agent combine direct PDF text or
OCR readings with the original drawing image and engineering checks:

1. **Use embedded PDF text when available.** Obtain original text and character
   locations, retain the original PDF and rendered image, and map coordinates
   explicitly. The installed `pypdfium2` exposes `get_textpage`, `get_text_range`
   and `get_charbox`; its documentation also describes bounded Unicode text
   extraction. Current ingestion only renders PDFs. This route avoids OCR for
   accessible text, but outlined glyphs, custom fonts, missing Unicode mappings
   and image-only PDFs still require visual evidence. Extracted text is not
   automatically authoritative. See the [PDFium text API](https://pypdfium2.readthedocs.io/en/stable/python_api.html).
2. **For image-only inputs, collect positioned OCR evidence.** Preserve raw
   readings, text polygons, orientation and recognition scores. An OCR helper
   such as PaddleOCR exposes these outputs; it is not installed or selected for
   this project. OCR confidence alone is not a correctness certificate, and
   general document recognition does not establish GD&T or section understanding.
   Use only the necessary components after measuring hardware/dependency cost.
   See [PaddleOCR's documented outputs](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PP-StructureV3.en.md).
3. **Let the agent interpret engineering relationships.** Supply the source
   image, located readings and any disagreements to the medium-effort Domo
   worker. It still identifies view boundaries, projection symbols, section
   names and cutting planes, and connects dimensions to physical features.
   OCR text boxes must not silently replace the agent's drawing-view boxes.
   These are tools within the extraction worker, not new orchestration agents.
4. **Apply engineering consistency checks.** Parse quantities, tolerances,
   diameter/radius, units and supported thread forms without discarding raw
   notation. Validate references, conflicts and attachments across sections and
   external views. Derived values require stated evidence and assumptions.
   Consistency is a check, not proof that a source value was read correctly.
5. **Inspect disputed details at source resolution.** Revisit specific symbol or
   callout crops when evidence conflicts, subject to the full time budget. Keep
   uncertainty visible and preserve human review. Extra image enlargement cannot
   recover detail that was absent from the original.

PDFium is not safe for simultaneous calls from different threads, including
different documents. Any native-text extension must serialize PDFium use or
isolate parallel work in processes, including the rendering path. See its
[threading restriction](https://pypdfium2.readthedocs.io/en/stable/python_api.html#incompatibility-with-threading).

### Author clarification: verify both OCR readings and drawing images

The author requires the agent to cross-check OCR completeness and the images of
each drawing view. The hybrid flow must therefore carry two evidence channels:
located text readings and the original visual content. OCR output alone cannot
replace the geometry, hidden lines, hatching or view relationships in the source.
The author has agreed to proceed with this hybrid behavior; the OCR engine,
request default remain unselected; R3 compares both assessment sequences and
evidence storage uses S2.

**Visual channel:** The agent inspects the whole sheet to identify all views,
including orthographic, isometric, section, detail and partial/broken views. Each
identified view receives an ID, source geometry region and an actual crop of
the original image. A contextual region retains nearby dimensions, leader lines,
section captions, cutting-plane arrows and scale labels. Keep an unclipped
geometry crop and sufficient callout context; do not merge two physical views
merely because nearby annotations overlap. Projection symbols and BOM/note
regions may lie outside view crops and require their own source evidence.

These crops are evidence from the uploaded sheet, not newly generated diagrams
or images reconstructed from OCR text. Optional overlays are separate display
artifacts. Preserve source resolution and coordinate mappings. A crop that
excludes relevant geometry, captions or callouts is a coverage issue.

**Text channel:** Native PDF text or OCR supplies original readings and their
locations, including dimensions, tolerances, quantities and labels. Link each
reading to its printed source occurrence before linking it to views/features.
Text between adjacent views may be shared or ambiguous; do not assign it solely
to the nearest view or apply a correction to every matching number.

**Agent cross-check:** The medium-effort extraction agent must look at the
original image as well as the reading list. It inventories visible callouts and
visual features, checks every identified view, and compares those findings with
OCR. Record per-occurrence outcomes such as:

| Outcome | Meaning and next action |
| --- | --- |
| Matched | OCR and visual reading agree on the same source occurrence. Keep source and interpretation evidence; agreement does not certify global completeness. |
| Agent-only reading | A visible callout was missed by OCR. Retain the visual reading with its location and provenance; request confirmation when uncertain. |
| OCR-only reading | OCR produced a candidate not yet confirmed against the source. Inspect it and record whether it is a valid callout, other text or an error. |
| Conflict | The visual and OCR readings disagree on value, symbol, tolerance, quantity or attachment. Preserve both and ask a source-linked question if unresolved. |
| Unreadable/not checked | A region or callout cannot be assessed. Mark it explicitly and block dependent CAD requirements. |

Each view's coverage report records whether its geometry, dimensions, caption,
section relationships and relevant internal features were assessed, and whether
its image/callout context is clipped or ambiguous. Perform a whole-sheet sweep
for missed views, sections, symbols and annotations outside the initial crops.
An OCR omission must not prevent the agent from discovering an entire section.

Visual interpretation must distinguish outlines, holes, axes, hidden lines,
construction information and hatching. Use sections and external views together.
Record observed shape separately from numeric dimensions; never infer a CAD
length from pixel size or invent hidden geometry to complete a model.

**Handoff:** The user reviews the view images, readings, feature interpretations
and questions together. The SolidWorks worker receives the reviewed visual and
text evidence references plus validated constraints. Required conflicts or
missing measurements must be resolved before their geometry is accepted.

The cross-check is part of the 30/40-second budget. Per-view coverage does not
require a separate model call for every view; readable evidence can be batched.
The current Domo adapter accepts one image per call and may reduce oversized
images, so image packing, targeted crops and actual transmitted resolution must
be evaluated. Do not hide reduced readability or added calls to claim the target.

**Acceptance cases:** Deliberately omit OCR dimensions; misread a diameter or
tolerance; repeat equal values on different features; omit a section from initial
view detection; clip a view's dimensions/caption; and place a projection symbol
outside the view crops. Verify that the agent/app retains the relevant images,
exposes discrepancies and blocks unsafe downstream use. Compare against a
human-checked inventory: the same agent can miss an item in both channels, so
cross-checking alone cannot prove that every source mark was captured.

### Engineering-specific recognition as a separate candidate

Specialized training is another avenue if general OCR and visual interpretation
miss engineering notation. Research describes training region recognition and
document parsing for GD&T, measures, materials, radii, roughness, threads and
title blocks. Another study evaluates fine-tuning a small vision-language model
on expert-annotated engineering drawings for GD&T. These are reported results on
their datasets, not evidence that the models cover our drawings, sections,
projection symbols or 30/40-second targets. Annotated data, model availability,
licensing, hardware, maintenance and evaluation would need investigation.
[Engineering drawing parsing study](https://arxiv.org/abs/2505.01530),
[GD&T fine-tuning study](https://arxiv.org/abs/2411.03707).

Before selecting A/B/C's call arrangement, the broader architecture discussion
can compare these three unranked families:

| Family | Mechanism and trade-offs | Failure conditions |
| --- | --- | --- |
| Vision-led | Existing Domo worker reads images; choose or adapt A/B/C. Fewer new dependencies, but the model performs both character reading and engineering interpretation. | Fine detail or symbols are missed, relationships are invented, or inference exceeds the budget. |
| Source-aware hybrid | Native PDF text or positioned OCR provides additional evidence; Domo interprets the original drawing and checks relationships. Better source localization is possible, but coordinate alignment, OCR errors and helper runtime must be managed. | Source text/OCR is wrong or unavailable, text is attached to the wrong feature, or helper work plus Domo exceeds the budget. |
| Specialized recognition with agent interpretation | An engineering-trained recognizer supplies targeted symbol/callout evidence to Domo. Can address domain-specific errors, but adds dataset, training, deployment and maintenance work. | Unseen notation, drawing styles or categories exceed training coverage, or measured quality/runtime fails the agreed contract. |

The author has now agreed to the source-aware hybrid flow with mandatory agent
cross-verification of OCR and original view images. The other families remain
unselected alternatives; their retention is not an author rejection. Test the
same original inputs, repeated
callouts, tolerances, small symbols, projections and sections against checked
inventories. Measure wrong readings, omissions, source associations and complete
latency together. Additional evidence could improve accuracy or reduce repeated
full-page reads, but Domo inference alone may still exceed the budget.

### Timing, coverage and incomplete results

The [extraction gap audit](docs/egm/extraction-gap-audit.md) records the current
code gaps, offline reproductions and acceptance cases. In particular, fix
occurrence-count/text conflation and false completeness before adopting a faster
path. The hybrid design also needs structured engineering notation, scoped
notes/tolerances, view-image coverage, cross-sheet/component links and a checked
evaluation set. These are part of the design scope, not completed production
changes.

Record browser upload start, server acceptance, page ingestion, queue entry and
start, each provider request, validation, persistence and result availability.
Retain both complete-result latency and preview latency. The exact contractual
start point and treatment of multipage ingestion/queueing remain to be chosen;
these measurements prevent excluded work from being hidden.

Standard pages target 30 seconds. A complex page can receive one additional
10-second allowance, up to 40 seconds total. Define complexity from source
content before scoring; slow inference alone cannot change the page's class.
Classification itself belongs in the measured work. Thresholds and handling of
unclassified pages remain open.

Processing stage, evidence coverage, human review and CAD readiness must be
represented separately. A schema-valid result with questions may be ready for
human review while still blocked from CAD. A missed deadline is recorded even
if a later result becomes useful. Completion at the provider does not
retroactively satisfy the deadline or revive invalidated approval.

The technical design must choose whether to stop waiting, accept a late result
as a new reviewable attempt, or offer explicit continued processing. It must
also distinguish client timeout from actual provider cancellation; no unverified
assumption about cancellation support is made here.

### Correctness contract for the first implementation stage

The first stage must address occurrence reconciliation and extraction acceptance
before a faster extractor can become the default. These requirements apply
regardless of the evidence storage choice below.

**Preserve separate source occurrences.** Give every printed callout a source
identity within an immutable extraction revision, with its original text and
location. Keep OCR, native PDF and visual readings as separate observations of
that occurrence. A normalized numeric token may help search for candidates, but
must not establish identity or select the canonical raw text. Two printed `20`
callouts stay distinct; two readings of the same printed callout may be
reconciled only with evidence that they refer to that source location.

Keep printed occurrences separate from physical-feature identity and quantity.
Two views can show the same hole, while `4xØ10` can describe four holes from one
printed callout. Neither relationship can be inferred from occurrence counts.
Unknown relationships require an explicit unresolved item rather than an
automatic merge or multiplication of physical features.

**Retain competing readings without silent loss.** For legacy/unlocated readings,
preserve the original whole-page and crop entries and expose count, text or
attribution disagreement. A nonzero whole-page count must not suppress a higher
crop count. Merely replacing the preferred count with the largest count also
does not prove identity or full coverage. The upgraded path needs source-located
occurrences; unresolved legacy evidence cannot gain the stronger acceptance
label through numeric agreement alone.

**Preserve engineering meaning.** Raw `20 ±0.1` and `20 ±0.2` must remain separate
readings with their own tolerance interpretation and source attachment. Quantity,
fraction, reference-zero, angular and symbolic information remains available even
when it cannot produce a supported CAD length. Raw strings and interpretation
status must not be replaced by a nominal-only token.

**Validate evidence before accepting completeness.** A missing, malformed or
unassessed required view response creates a coverage issue. Retain that view's
source crop and the missing requirement. Warnings or cache invalidation alone
are insufficient. The result may expose useful partial data for review, but
must not become evidence-ready or plan-ready while required gaps remain.

For new clients, represent four independent dimensions rather than overloading
one `complete` flag:

| Dimension | Required distinction |
| --- | --- |
| Processing | Queued/running, finished, failed, interrupted or timed out. |
| Evidence coverage | Assessed versus missing, malformed, unreadable or conflicting required evidence; include per-view/category details. |
| Human review | Pending, corrections applied, or reviewed against an exact evidence revision. |
| CAD readiness | Ready only for supported, approved requirements with required evidence resolved; unsupported geometry remains a blocker. |

Exact serialized values and API negotiation will implement the selected S2
storage/versioning contract. Existing legacy completion fields retain their
old meaning until that negotiation is designed. A new client must not interpret
an old processing-complete flag as proof of upgraded evidence acceptance.

**Required regression cases before a default-path change:**

| Case | Required observable result |
| --- | --- |
| Page reader sees one `20`, crop reader sees two. | Preserve the discrepancy and all supplied readings; do not silently drop the additional candidate. Resolve source identities before accepting coverage. |
| Equal nominal values carry different tolerances. | Each located occurrence retains its own raw text, tolerance and provenance. No first-string reuse across occurrences. |
| Two overlapping crops contain the same printed callout. | Record both readings against that source occurrence when location evidence is sufficient; uncertainty must not become a silent duplicate or merge. |
| One callout appears in different views of the same feature. | Preserve both printed occurrences and separately justify the physical-feature link. |
| A callout contains zero, a fraction, a quantity or a symbol. | Preserve and classify its full meaning or require clarification. Do not split a fraction into unrelated dimensions or discard a valid zero reference. |
| One expected crop response is absent or malformed. | Keep source evidence visible, record the missing assessment and block dependent CAD readiness. |
| OCR and visual agent miss the same source annotation. | The checked benchmark inventory records the omission; agreement alone cannot pass the quality gate. |
| A source correction changes diameter, tolerance or feature attachment. | Invalidate dependent interpretation and approval; other equal-valued source occurrences remain unchanged. |

### Evidence storage/versioning: S2 selected by the author

The author selected S2. The comparison remains below to record the trade-off;
S1 is unselected, without attributing an unstated rejection rationale to the
author. Both approaches preserve the legacy execution paths.

| Option | Mechanism | Trade-offs | Failure conditions |
| --- | --- | --- | --- |
| **S1. Evolve the canonical drawing model** | Introduce a new model version containing source occurrences, typed interpretations, coverage and review state. Add explicit readers/adapters for old saved results and negotiate the new API version. | One primary engineering representation; requires migration of model consumers, hashes, corrections, planning and UI contracts. | An old client silently drops new evidence, a migration changes approved meaning, or adapters make unsupported/missing data appear accepted. |
| **S2. Add a versioned evidence sidecar** | Store the richer evidence as an immutable artifact linked to the existing source/result revision. An adapter produces compatible legacy views; new reviews/plans bind to both the source/model and evidence digest. | Keeps existing source artifacts and legacy model shape usable; requires strict linkage and authority rules between the richer evidence and compatibility views. | Stale/mismatched artifacts are joined, duplicated mutable values diverge, or a legacy approval bypasses the new evidence requirements. |

Under S1, derived compatibility views must not become an alternative mutable
truth. Under S2, the source/evidence pair must be validated before it is exposed
as a usable revision, and corrections must target one authoritative reviewed
evidence layer. Both must preserve old jobs under their original contracts,
mark missing legacy evidence as not recorded, and invalidate new approvals when
required evidence changes.

The selected S2 contract follows. Existing feature-enrichment sidecars remain
distinct: their mutable processing reports do not satisfy this richer immutable
evidence contract merely because they are also separate JSON files.

### S2 artifact layout and authority

Proposed paths fit the existing local artifact-store pattern:

| Artifact | Role |
| --- | --- |
| `storage/web/<result_id>.json` | Existing immutable source reading; retain its historical shape and hash semantics. |
| `storage/evidence/<result_id>/<revision_id>.json` | New immutable evidence revision: source occurrences, readings, interpretations, view/image references, coverage, questions and human decisions. |
| `storage/evidence/<result_id>/latest.json` | Small validated revision/digest pointer; never an alternative editable copy of the evidence. |
| `storage/evidence/blobs/<sha256>` | Retained original-resolution crop/context image artifacts addressed by content hash. They must survive ordinary cache eviction while the evidence is retained. |
| Existing run/enrichment artifacts | Processing progress and diagnostics; cannot mutate an accepted evidence revision or establish its correctness by status alone. |

The richer evidence revision is authoritative for the S2 workflow's reviewed
engineering meaning. The existing result remains authoritative for the original
raw reading. A compatibility `DrawingModel` is a derived view, never a separately
editable source of truth. Numeric and symbolic corrections target source
occurrences in the evidence revision, not both stores independently.

The evidence envelope records its contract version, result ID, original result
digest, page/image digests, revision ID, optional parent revision, worker/model
configuration and extraction-contract versions. Its content contains located
occurrences and their raw readings; typed interpretations reference those
occurrences; feature/view relationships reference these records rather than
copying independently mutable values. Coverage, questions and review decisions
remain explicit. Processing progress is kept outside the immutable revision.

Define a deterministic digest of the validated envelope excluding any field
that stores that digest itself. Retain original image-to-crop transforms, units
and source text. Validate content and referenced blob hashes before accepting a
revision. A missing artifact, missing required image, unsupported schema or
source-digest mismatch produces a visible unavailable/needs-review outcome.
It must not silently fall back to an apparently accepted compatibility view.

### S2 revisions, correction conflicts and publication

Every extraction update or human correction produces a new revision. Existing
accepted files are never overwritten. A correction carries its expected parent
revision and digest; publishing it checks that the expected parent is still the
active revision. A stale edit fails with a refresh/reconciliation requirement.
Unrelated equal-valued occurrences retain their own IDs and readings.

For the current single-process server, serialize per-result revision publication
around parent checking, immutable-file creation and atomic pointer replacement.
Write/validate the revision and required assets before replacing `latest.json`.
Readers accept only a fully validated source/revision pair. A crash before
pointer publication may leave an unreferenced artifact but must not expose a
partially committed revision. Multiple server writers would require separate
coordination work; S2 does not assume a process-local lock provides that.

Background model work also carries its expected parent. A late response cannot
overwrite human corrections or resurrect invalidated approval. Keep a rejected
or conflicting response as an attempt/diagnostic artifact for explicit review.
The timing/retry decision still determines whether and how users can continue
after a deadline; revision publication alone does not select that policy.

### S2 review, planning and API binding

Retain current routes. Evidence-aware responses expose an explicit contract
version and a binding containing result/source identity, evidence revision and
digest, and the derived reviewed-model hash. Clients submit that binding with
corrections and plan requests. The server reloads and verifies the referenced
artifacts and dependency hashes before accepting the action.

The adapter preserves each source occurrence's identity in a revision-bound map
to compatibility dimension/view IDs. It must not recreate ambiguous value-only
identity or hide unsupported symbolic requirements. No legacy schema field
should be overloaded to suggest that unrecorded evidence has been verified.

New S2 plan approval binds the evidence revision/digest, reviewed compatibility
model and actual proposed plan. Preserve the existing geometry-list hash and
native receipt checks; carry the additional evidence binding through the S2
workflow artifact and validate it at build and export boundaries. Any changed
required evidence, review or geometry invalidates dependent S2 approval.
Final model/source comparison and explicit export confirmation remain required.

Old results and approved jobs continue under their original contract and are
labelled as having legacy/not-recorded evidence coverage. Missing S2 fields
must not cause old clients to receive an implicit stronger approval. Conversely,
an old client cannot approve a new S2 attempt by omitting its evidence binding.
Exact capability negotiation and request fields will be specified in the
implementation plan after the rest of the Technical Plan is resolved.

### S2 acceptance checks

* Reopen an existing result/job without rewriting its source or approval hash.
* Reject a sidecar linked to a different result, source or page image.
* Preserve distinct equal-valued callouts and their differing tolerances.
* Apply numeric and symbolic corrections only to their intended occurrences.
* Reject stale edits and background updates racing with human corrections.
* Keep the last valid revision readable if publication is interrupted.
* Detect altered or missing evidence and crop bytes before downstream use.
* Invalidate a plan when required evidence changes, even if nominal geometry
  happens to remain the same.
* Reject build/export through a stale or omitted S2 evidence binding.
* Preserve legacy behavior without presenting it as S2-verified extraction.

### Hybrid model-call sequence: R3 selected by the author

The author selected R3: compare R1 and R2 before selecting a production default.
S2 supports both sequences. OCR/native text collection and all validation/crop
work count toward complete latency. The Domo worker remains at medium effort,
and human review remains required. The options below retain their trade-offs.

| Choice | Mechanism | Trade-offs and failure conditions |
| --- | --- | --- |
| **R1. Joint visual/OCR assessment** | Give the original image and located readings to one combined agent assessment for views, symbols, features and discrepancies. Use deterministic reference/coverage checks afterward. | Fewer mandatory model calls; the agent can still be biased by OCR or miss the same annotation. Failure occurs if required detail is unreadable, omitted or incorrectly associated, or the complete result misses its time budget. |
| **R2. Separate visual verification** | Produce the structured candidate, then perform an explicit source-image verification pass against it before accepting evidence. | Makes a second assessment and discrepancies observable; adds a dependent call and can still share omissions. Failure occurs when verification misses source content or pushes full extraction past 30/40 seconds. |
| **R3. Compare both before selection** | Keep R1 and R2 as experiment paths on the same checked sources, model and effort; select a default only after measuring complete latency and category-level quality. | Defers a production choice and requires more experiment work/calls; fails to settle a default if neither meets the agreed quality/timing contract. This is not permission to run live calls before the experiment is specified. |

The choice approves the comparison direction, not claims about accuracy or
speed. Both sequences retain per-view visual assessment and OCR/agent discrepancy
records. The [comparison protocol](docs/egm/extraction-comparison.md) specifies
controlled inputs, phase boundaries, quality measures and result handling.
Neither sequence becomes the default merely because it uses fewer calls or
because its second pass returns without an exception.

### Evidence handoff and interpretation dependency checks

CAD planning consumes the reviewed evidence revision, feature bindings and
remaining questions. A previously produced interpretation is reusable only when
the source, reviewed corrections, schema/prompt versions, extraction approach,
model and effort match its dependencies. A correction invalidates affected
interpretations and downstream approvals. Unsupported geometry remains recorded
and blocks a supposedly complete build.

The exact reuse mechanism remains open: dependency-checked artifact reuse or
re-interpretation after review both need a defined compatibility path. Arbitrary
human clarification cannot be assumed to have a deterministic local update.
Reuse does not count as proof of new-page extraction latency.

### CAD intent and native verification

Keep the existing supported recipes and before/after checks. Enrich their
operation records so generated lines, arcs, construction geometry, constraints
and features reference their engineering purpose and approved requirements.
Grouped recipe execution can supply these records without separate model calls
for individual lines.

For example, a reviewed blind-hole callout and section depth establish the
nominal diameter, location, axis and depth requirements. The operation record
connects those requirements to the circular profile and blind cut. Native checks
must read the resulting dimensions and verify blind termination and expected
material change where the required APIs support it. A successful tool response
or plausible volume alone does not satisfy the full check set.

Keep manufacturing tolerances as source requirements. Define numerical
comparison tolerances independently for API/kernel precision and record the
measurement method. Required checks that lack API support remain `not checked`
and block acceptance. Broader geometry and new measurement methods require
licensed SolidWorks acceptance, including internal section-derived features.

### Compatibility and interface boundaries

Retain existing read/review/plan/workflow endpoints, the non-streaming JSON path
and streamed progress behavior for existing clients. Richer evidence and states
need an explicit version/capability boundary; do not silently alter the meaning
of existing completion flags or approved plan hashes.

Historical results remain readable and missing legacy evidence is labelled
`not recorded`. Re-extraction creates a new immutable revision. Cross-revision
correction mapping requires source evidence and confirmation for ambiguity.
Existing approved jobs remain pinned to their original interpretation and
execution contract.

Preserve the shared provider request limit and native writer lock. This design
does not assume that extra agents or processes improve latency. Admission,
retry and call-budget changes must be measured against the same account and
model constraints as extraction.

### Remaining decisions within this section

1. Develop the agreed hybrid flow with S2 and the selected R3 comparison of R1
   and R2. Choose OCR tooling and finalize the protocol prerequisites before
   live comparison. Keep agent-generated view boundaries and mandatory
   visual/OCR coverage checks. A production default remains undecided until the
   quality/timing comparison supports one.
2. S2 is selected. Retain its artifact authority, revision and approval-binding
   rules when specifying API fields and implementation files.
3. Agree timing boundaries, complexity rules and deadline/recovery behavior.
4. Set interpretation reuse rules, model-selector scope and initial measurable
   CAD capabilities for the checked reference case.

The Technical Plan is not ready for section approval until its consequential
architecture decisions have been resolved. Alternatives and implementation
inventories will reflect the author's actual choices.

*Drafted with EGM*
