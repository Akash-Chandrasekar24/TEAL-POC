# R3: compare joint assessment and separate visual verification

* **Author:** Akash-Chandrasekar24
* **Date:** 2026-11-05
* **Decision:** R3 selected; no production default selected.
* **State:** Initial single-page live trials completed after explicit implementation/testing authorization. See [measured results](../R3_EXTRACTION_RESULTS.md); production acceptance remains pending.
* **Design:** [Technical Plan](../../design_draft.md).

## Question the comparison must answer

Does R1 or R2 produce the required complete, correctly associated engineering
evidence within 30 seconds for standard pages and 40 seconds for complex pages,
using the current Domo model at medium effort? If neither qualifies, report that
outcome. R3 does not require choosing a winner when both miss acceptance.

The comparison covers extraction, including all required symbols, sections,
projection evidence, features and applicable component/BOM content. CAD remains
at high effort but its build time is separate from the extraction trial.

## Fixed inputs and controls

* Use the same original source bytes, image transformations, OCR/native-text
  configuration, model, effort, evidence schema and deterministic validators.
* Retain all source images, actual transmitted image hashes and source/crop
  transforms. Do not improve one path's image quality without recording and
  comparing that change separately.
* Use the same symbol/feature coverage requirements and human-checked inventory.
  Both paths must discover details omitted by OCR and report disagreements.
* Keep the existing provider concurrency limit; record provider contention,
  queuing, retries and failed requests. Alternate paired trial order to reduce
  ordering bias; do not claim this removes provider variability.
* Preserve the existing pipeline as an additional control with its actual
  quality and full latency, not only historical timings.
* Keep candidate attempts and S2 revisions separate. Do not reuse human
  corrections or findings from an earlier trial as hidden hints to a later one.

## Paths under comparison

### R1: joint visual/OCR assessment

Collect native PDF text or positioned OCR evidence. Give the medium-effort
agent that evidence and the original visual material in a combined assessment.
It returns located observations, engineering interpretations, view/section and
feature relationships, OCR discrepancies, category coverage and questions.

Validate structure, reference integrity, units, occurrence identity, conflicts
and required coverage before publishing the reviewable S2 result. One valid JSON
response cannot by itself certify visual completeness. Additional image packing
or provider requests required by image limits remain part of its measured work.

### R2: candidate followed by visual verification

Create the same kind of candidate as R1, then supply the original visual
material, OCR evidence and candidate to an explicit verification request at
medium effort. Ask for missed views/callouts, incorrect symbols, quantity or
tolerance errors, source/feature misattachments and unresolved details.

Verification may add a source-located occurrence, propose a correction or
dispute a candidate. Preserve the candidate and verification readings; apply
validated changes through a new S2 revision. Do not silently delete competing
evidence or mark disputed requirements accepted. Revalidate coverage and all
references before publishing the final trial result.

This is a second assessment of shared source material, not proof of independent
truth. The verifier can be biased by the candidate and miss the same details.
The experiment evaluates this failure mode against the checked inventory.

## Two distinct measurement phases

1. **Controlled agent comparison:** Freeze identical OCR/native-text artifacts
   and visual inputs for a pair of R1/R2 trials. Measure their differing model
   and validation work. This isolates the effect of the verification pass; it
   is not an end-to-end latency acceptance test and cannot prove 30/40 seconds.
2. **End-to-end comparison:** Run source ingestion, OCR/native text, model work,
   validation, persistence and availability for each path. Disable model/result
   cache reuse for new-page timing. Record OCR nondeterminism if the same pinned
   pipeline produces different readings. Report cold startup and an already
   loaded OCR service separately; neither may be hidden in a performance claim.

Capture browser upload start, server acceptance, ingestion start/finish, OCR
start/finish, queue entry/acquisition, each provider request/reply, validation,
revision persistence and result availability. The contractual start boundary,
load, repeat count and percentile must be agreed before pass/fail scoring.

Record results that exceed the deadline and any partial result available at
30/40 seconds. Full completion after the deadline remains a latency failure.
Whether to stop waiting or accept a late attempt for user review is a separate
runtime policy; no provider-cancellation capability is assumed.

## Source selection and checked inventory

Select exact source hashes before the trial. Existing candidate inputs include
`inputs/image_11_triple_clamp.png` for continuity with the recorded detailed-page
timing, plus representative files from `inputs/` and the user's section/detail
sheets. These are candidates, not verified truth or final complexity labels.

The existing `eval/benchmark.json` marks its annotations as drafted/unverified.
An engineer must check the selected source inventory before quality scoring.
Include examples with repeated dimensions, differing tolerances, small/rotated
callouts, projection symbols, internal sections and component/BOM references.
If the current sources do not contain a required annotation family, obtain a
suitable checked case rather than claiming it was tested.

For each source, inventory views and their extents; original-resolution image
and context regions; printed callout occurrences and locations; quantities and
tolerances; symbols/datums; section/cutting-plane relationships; feature
attachments; relevant notes/materials/BOM content; and genuinely unreadable or
missing information. Record absence explicitly where verified.

Fix standard/complex labels and their content-based rationale before timing.
Slow inference alone cannot reclassify a standard page as complex. Exact
complexity thresholds and quality acceptance levels remain open author choices.

## Quality, latency and usability results

| Measure | What to record |
| --- | --- |
| Complete latency | Per-run elapsed milestones through full result availability, plus misses/failures; separate preview and agent-only phase measurements. |
| Occurrence coverage | Missed, extra or duplicated source callouts and views, including equal-valued callouts. |
| Reading correctness | Exact engineering content: nominal values, units, quantities, tolerances, thread/fit details and symbolic annotations. |
| Interpretation/attachment | Whether readings belong to the right physical feature, section, view, component and page; distinguish unsupported build capability. |
| View-image coverage | Missing views, clipped geometry/callout context, absent captions and unreadable transmitted detail. |
| Discrepancy handling | OCR-only, agent-only, conflicting and unreadable evidence; false acceptance versus properly raised questions. |
| Human burden | Targeted questions and corrections needed; retain legitimate source uncertainty separately from avoidable extraction mistakes. |
| Provider/helper work | Model calls, retries, available reasoning/output/input usage, queue time, OCR time, hardware and initialization costs. |
| Compatibility | Source immutability, revision linkage, stale-update rejection and approval invalidation under S2. |

Score categories and source attachment separately. Nominal-token agreement and
confidence scores cannot stand in for quantities, tolerances, symbol meaning or
image coverage. Do not hide failed or incomplete runs by reporting latency only
for successes. Report observed distributions without inventing a p95 guarantee
from too few trials.

## Decision rule and outstanding prerequisites

Compare quality first against the agreed requirements, then timing and cost.
A faster path with required omissions does not qualify. A slower path that
finds more evidence may inform further design but does not pass the deadline.
If one qualifies, document its limits and rollout/rollback plan before changing
the default. If both qualify, the author selects the production trade-off. If
neither qualifies, retain the current default and report measured limitations.

Before live trials, finalize the OCR adapter and supported runtime, checked
source inventory, category thresholds, clock boundary, repetition/load/cost
limits and timeout policy. R3 selects comparison as the next direction; it does
not imply that these prerequisites, production implementation or timing claims
are already approved or complete.
