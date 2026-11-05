# Measured R1/R2 extraction results

Follow-up: [30-second enhancement trials](EXTRACTION_SPEED_ENHANCEMENTS.md)
record native OCR/connection reuse, response-format experiments, failures and
the remaining latency limits. The original measurements below are unchanged.

## What was implemented

The extraction worker now has two evaluation paths using the selected Domo
model at medium effort:

* **R1:** local positioned OCR plus one joint visual assessment returning
  measurements, symbols, views, section evidence, features, coverage and questions.
* **R2:** its own joint assessment followed by a visual verification request
  using the source overview and packed original view crops. Additional sheets
  would add calls. Candidate evidence and unresolved questions cannot disappear
  silently during verification.

The implementation uses installed Windows OCR. Actual source view images and
rich evidence are retained in immutable S2 revisions with content digests,
source bindings and optimistic parent checks. Distinct callouts retain separate
raw text, including differing tolerances; this path does not use the legacy
value-only merge. Missing coverage, unknown references, OCR disagreements and
overlapping views require attention. Human review remains pending.

The upload page offers both modes. Existing extraction remains the default.
Evaluation results cannot start or export CAD through part or assembly workflow
routes. The existing CAD worker remains at high effort. This release implements
the comparison/evidence collection, not complete S2 correction-to-CAD acceptance.

## Test conditions and reference quality

These were **live, uncached provider calls**, not mocked timings. All used the
same configured `domo.openai.gpt-5.6-sol` model and medium extraction effort.
Whole-input timings include ingestion, OCR subprocess startup/recognition,
model work, validation and retained-evidence publication. OCR took approximately
2–4 seconds during these trials. Timings are single observations, not averages,
p95 values or a service guarantee. Some trials on different sheets could overlap
at the provider; they are not a controlled-load performance distribution.

The real-image references were visually transcribed by the assistant from the
source, including its retained original-resolution main-view crop for the dense
case. **They are not independently engineer-verified.** Their hashes pin the
actual input images. The synthetic fixture has machine-generated printed labels.
Neither source establishes general mechanical correctness.

Text scoring preserves quantities, tolerances and duplicate occurrences. It
normalizes whitespace and limited equivalent glyph forms; it does not reduce
everything to nominal tokens. A match means that a printed measurement string
was recovered, not that it was attached to the correct physical feature.

## Results

| Input and scored scope | Path | Full time | Matched expected measurement texts | Text recall | Text precision | View count |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Real support block, `inputs/image_9.png` | R1 | 38.571 s | 19 / 19 | 100% | 100% | 4 / 4 |
| Same support block | R2 | 83.418 s | 19 / 19 | 100% | 100% | 4 / 4 |
| Synthetic plate with tolerances, quantity and section A-A | R1 | 33.598 s | 6 / 6 | 100% | 100% | 2 / 2 |
| Same synthetic plate | R2 | 60.434 s | 6 / 6 | 100% | 100% | 2 / 2 |
| Dense triple clamp, `inputs/image_11_triple_clamp.png` | R1 | 74.880 s | 51 / 53 | 96.23% | 96.23% | 7 / 7 |
| Same dense triple clamp, completed retest | R2 | 163.030 s | 50 / 53 | 94.34% | 92.59% | 7 / 7 |

R2 produced 54 measurement entries for the dense reference's 53 callouts,
including an extra `16` retained after verification omitted an existing source
identity. The reconciliation guard raised a question rather than silently
dropping candidate evidence.

R1 was faster on all three sheets. R2 did not improve exact measurement-text
recovery on these cases and was worse on the dense case. This is a small sample
and cannot establish that R1 is always more accurate or that verification never
helps.

## Important errors and untested meanings

* On the dense source, the main view's rotated `Ø155` and `Ø135` callouts were
  read as `15°` by R1. R2's completed retest returned `R50` readings instead.
  These are important diameter/radius/angle interpretation errors.
* The completed dense R2 retest also read the lower main-view `8°` as `6°` and
  retained an extra `16`. It raised several uncertainties, but uncertainty
  reporting is not the same as accurate extraction.
* Both dense paths retained the local `55mm` section-depth note and an A-A
  section reference. The global unit convention was not independently certified.
* Both synthetic paths recovered the six complete measurement strings, including
  `100 ±0.1`, `20 ±0.2` and `4xØ10 THRU`, the two views, printed units and
  third-angle text, and section A-A. No graphical projection symbol is present.
* R1 noted that the controlled sheet lacks dimensions locating the right hole
  column and lower hole row. The synthetic R2 trial did not report that same
  gap. Thus 100% measurement-text recovery does not prove complete engineering
  understanding, even on this small controlled sheet.
* Correct view count does not certify crop boundaries or full geometry/callout
  coverage. Dense rectangular crop regions overlap; the current validator flags
  significant overlap for review rather than treating it as certified separation.

Full GD&T, datum/finish/welding semantics, feature attachments, all required
manufacturing information and actual CAD geometry were **not accuracy-certified**
by these tests. No licensed SolidWorks reconstruction was part of this comparison.

## Failures and scoring correction

The first dense R2 attempt failed after 67.578 seconds because the model returned
null units for some callouts. The parser was updated to preserve that as unknown,
not guess a unit, and a full R2 retest completed. Failed attempts remain in the
results; they must not be hidden from reliability evaluation. Verification was
also hardened to retain earlier questions and candidate observations.

An initial support-block reference-writing command encoded `Ø48` as `?48`.
That reference error was corrected against the displayed source and outputs
were rescored without another model call. Original trial reports/replies remain
unchanged; `rescored-results.json` records the updated evaluation and reference
digest. The table above uses these corrected scores.

Later deterministic checks, including overlap review and native-workflow guards,
were verified offline. They do not turn the earlier model responses into new
measured provider trials.

## Verification

The final offline suite passed **202 tests**, including typed evidence/reference
validation, distinct tolerance occurrences, question/occurrence preservation,
overlap review, S2 stale-parent and concurrent-publication conflicts, source/blob
tamper detection, retained-image serving after cache misses, actionable HTTP
errors, and blocking trial evidence from native CAD workflows. Drawing review,
assembly and extraction-stream UI smoke checks passed. A hidden-browser capture
of the imported live support-block trial confirmed the source image and agent
view boxes render alongside the readings. These checks establish implementation
behavior, not recognition accuracy or native CAD fidelity.

## Reproducible artifacts

* [Support-block reference](../eval/hybrid_support_block_reference.json)
* [Dense reference](../eval/hybrid_triple_clamp_reference.json)
* [Synthetic generator](../eval/hybrid_fixture.py)
* [Comparison runner](../eval/compare_extraction.py)
* [Exact text scorer](../eval/hybrid_score.py)
* Raw responses, OCR readings, stage traces, retained view crops, S2 revisions,
  original reports and corrected scores: `eval/runs/hybrid-support-block-live/`,
  `eval/runs/hybrid-synthetic-live/`, `eval/runs/hybrid-triple-clamp-live/`,
  and `eval/runs/hybrid-triple-clamp-retest/`. Runtime outputs are git-ignored.

Saved live trials can be inspected in the local UI:

* [Support-block R1](http://127.0.0.1:8765/#ad5fc55a5244)
* [Support-block R2](http://127.0.0.1:8765/#ef75fa367ad9)
* [Dense R1](http://127.0.0.1:8765/#8a0d82becc5f)
* [Dense R2 retest](http://127.0.0.1:8765/#1b225687b837)

## Outcome

The comparison is implemented and exercised, but the performance/quality target
is **not met across the tested inputs**. No trial finished within 30 seconds;
only support-block R1 finished within 40 seconds. Complexity labels were not
pre-agreed, so this is not a validated 30/40-second acceptance result. Neither
path should be promoted as a generally reliable engineering extractor yet.

Further work should address rotated diameter/angle recognition and exact source
associations, improve checked reference coverage, and evaluate a faster reading
representation or model only with the required author decision. Keep the current
reviewed workflow available and preserve human correction/export gates.
