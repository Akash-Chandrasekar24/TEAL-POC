# Extraction gap audit

* **Date:** 2026-11-05
* **Scope:** Current production code versus the proposed OCR-assisted visual
  extraction contract. Static inspection plus small offline reproductions.
* **Outcome:** Gaps recorded for the design and implementation plan. No production
  files changed, OCR packages installed, provider calls made or native CAD run.

## Confirmed merge and normalization problems

These examples call the current `dim_tokens` and `combine` functions directly.
They demonstrate behavior on supplied readings, not recognition accuracy on an
image. Original page/crop reading lists remain available even when merged rows
lose a distinction.

| Supplied evidence | Current observed result | Engineering risk |
| --- | --- | --- |
| Whole-page read contains one `20`; a crop contains two `20` callouts. | `combine` produces one dimension row because a nonzero whole-page count wins over the crop count. | A callout missed by the whole-page reading can remain missing after reconciliation. |
| Both reads contain `20 ±0.1` and `20 ±0.2`. | Two merged rows are produced, both with `20 ±0.1` as their row text. | Separate tolerances can be conflated because normalized values share the first stored string. |
| `0` | `dim_tokens` returns an empty list. | A printed zero reference can disappear from dimension rows. It must remain a source observation even if it is not a physical length. |
| `1/2` | `dim_tokens` returns `1` and `2`; `Dimension.nominal` separately refuses fractional callouts. | Human confirmation prevents some unsafe CAD use, but the extraction representation does not preserve a structured fractional value. |
| `4xØ10` | Matching token is only `Ø10`. | Raw text retains quantity, but token matching/scoring does not establish four holes or their attachment. |

Evidence: [dimension tokens](../../app/perception/dims.py),
[combination](../../app/perception/extract.py),
[nominal parsing and reconciliation](../../app/drawing/model.py).

## Remaining gaps and required acceptance cases

| Priority | Gap confirmed or boundary identified | Evidence | Required closure/test |
| --- | --- | --- | --- |
| P0 | Printed occurrence identity and multiplicity are value-based. Equal values inherit overlapping view references and can lose count or text distinctions. | `combine`; `reconcile`; reproductions above. | Distinct source identities and reference locations; omissions, repeated values, differing tolerances and overlapping crops must reconcile without silent merging or duplication. |
| P0 | Complete processing is not complete evidence. Missing crop replies warn and leave default views; enrichment becomes `complete` when its work returns without an exception, including results with questions. | `read_page` in `app/perception/extract.py`; `_enrich_result` in `app/web/server.py`. | Explicit per-view/category coverage and missing-reply failures; distinguish processing, unresolved review and accepted evidence. A missing view/callout must block dependent CAD use. |
| P1 | Numeric engineering meaning lacks a full structured contract. Raw strings survive, but typed dimensions do not model quantities, tolerance forms, fits, fractional/dual units and angular versus length semantics comprehensively. | `Dimension`, `Dimension.nominal`; `dim_tokens`. | Typed interpretations beside raw evidence; test zero/reference values, fractional inches, signed/deviation forms, limits, hole quantities, angles and dual dimensions without inventing conversions or requirements. |
| P1 | Specialized annotation attachment is incomplete. Generic text/notes may contain GD&T, datums, roughness, welding or thread information; the inspected schema does not establish validated records and attachments for each category. | `PageRead`, `DrawingModel`, `FeatureObservation`. | Preserve and review each annotation, its raw appearance, related feature/datums and interpretation status. Uninterpreted symbols remain visible and block requirements they affect. |
| P1 | Section/projection reading exists, but relational verification is limited. Section evidence is a dictionary; `same_as` is restricted to another view on the same page. | `CropView`; `View`; `read_page`; `reconcile`. | Resolve cutting-plane/parent-view identity and cross-sheet references, including removed/detail sections. Preserve existing projection conflict review. Test a section missed from the initial inventory. |
| P1 | View images have regions and packed crops but no complete verified image-artifact/coverage contract. Fixed crop padding and downscaling can omit annotation context or reduce small-text readability. | `pack`; `CropView`; Domo `_prepare_image`. | Preserve original geometry/context crops and reversible transforms; verify captions, leaders and callouts stay associated. Record actual model input resolution. Reject clipped/unassessed views as complete evidence. |
| P1 | General notes and defaults are not systematically attached to affected dimensions. Notes are retained as text, but scoped general tolerances and explicit local overrides need a modeled relationship. | `Page.notes`; `Dimension`; title-block dictionaries. | Test a general tolerance applying to several dimensions, a local override and a conflicting note. Retain scope and provenance; never guess a standard's numeric limits. |
| P1 | Recognition vocabulary is coupled to compiler support. Feature kinds are plate, hole, boss or unsupported. | `FeatureObservation`, `Kind` in `app/drawing/planner.py`. | Preserve richer recognized threads, grooves, cavities, steps and patterns independently of whether the CAD compiler can build them. Keep unsupported-build blockers. |
| P1 | OCR-assisted cross-verification and native PDF text extraction are proposed, not integrated. PDFs currently become images, and the perception path uses model reads without positioned OCR evidence. | `app/ingest/pages.py`; `app/perception/extract.py`; `requirements.txt`. | Select an OCR/tooling design; compare located text and original visual content, detect OCR-only/agent-only/conflicting readings, and retain human corrections. Helper readings must not replace agent view boundaries. |
| P1 | Cross-page/component relationships need stronger source identity. Feature interpretation validates per-page view/dimension references; BOM evidence holds page and text, without a general balloon-to-feature source-region contract. | `interpret`; `PrintedRow`, `BOMRow`, `Evidence` in `app/assembly/`. | Link component IDs, balloons, BOM rows, part detail views and continuation references. Distinguish repeated views from multiple physical components; expose conflicting sheet revisions or missing continuation content. |
| P1 | Evaluation cannot certify full engineering extraction. Dimension scoring uses the same normalized tokens and cannot establish retained tolerances, quantities, symbol semantics or correct attachments. | `eval/score.py`; `app/perception/dims.py`; existing benchmark caveats. | Engineer-checked source inventories; category-level omissions, wrong readings/interpretations, occurrence links, view coverage and correction burden, alongside full latency. |
| P2 | Readability checks are coarse. Page pixel dimensions alone do not establish that tiny, rotated, obscured or densely packed annotations are legible. | `source_quality`; `pack`; Domo image preparation. | Evaluate original and transmitted readability per relevant region, record transformations and handle unreadable sources explicitly. Do not count enlargement as recovered missing detail. |
| P2 | Revision and correction compatibility need a richer-evidence migration. Current correction targets and IDs are scoped to saved extraction structure; existing hashes help prevent stale use but do not supply cross-revision occurrence mapping. | `reconcile`; `review_result`; `chat_reply`; `result_with_design`. | Preserve immutable readings, validate revised identities and invalidate dependent interpretations/approvals. Never transfer corrections to equal-valued callouts by value alone. |
| P2 | The 30/40-second targets are unproven and not implemented as the proposed extraction contract. Current dependent reads and subsequent enrichment exceed them in recorded runs. | `docs/EXTRACTION_PERFORMANCE.md`; `run`; `_enrich_result`. | Agree clock boundaries and complexity criteria, include OCR, image handling, queuing and cross-checks, and measure uncached full results. Late/partial responses remain explicit failures against the timing contract. |

P0 items can corrupt the meaning or declared completeness of the result and
should be addressed before an optimized extractor becomes the default. P1
items define the requested engineering coverage; P2 items establish operational
and migration acceptance. Priorities do not authorize implementation or remove
the current checks and review gates.

## Limits

This is not an exhaustive recognition evaluation. Static inspection cannot
establish whether a model will find every drawing mark. A human-checked source
inventory remains necessary, including for cases where OCR and the visual
agent make the same omission. The OCR engine, storage contract, request schedule,
benchmark thresholds and final Technical Plan approval remain outstanding.
