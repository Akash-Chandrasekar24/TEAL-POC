# Extraction latency checks

Measured locally on 2026-11-04 using Domo `domo.openai.gpt-5.6-sol`.
These are individual observations, not a latency guarantee or an accuracy score.

## Same-drawing comparison

Input: `inputs/image_11_triple_clamp.png` (1672 × 941).
Both runs used agent-generated view boundaries and independent whole-page and
crop readings. Neither initial reading used cached model answers.

| Stage | High extraction effort | Medium extraction effort |
| --- | ---: | ---: |
| Measurements ready | 160.7 s | 98.8 s |
| Subsequent feature interpretation | 157.3 s | 65.7 s |
| Combined processing | 318.0 s | 164.5 s |

Measurements became available about 39% sooner; combined processing fell about
48%. The browser benchmark observed full completion at 166.6 seconds because it
polled for background completion every three seconds.

The high run is `9085748e6349`; the medium run is `dfcab4143ee9`.
Raw readings and separate enrichment artifacts are under `storage/web/` and
`storage/extraction/`; call timings and effort settings are in `storage/runs/`.
The medium run returned seven view boxes and 59 dimension observations.
Observations still require source comparison and human review; counts do not
establish dimensional correctness or CAD reconstruction accuracy.

## Scheduling and validation

A separate live four-page upload (`inputs/image_1.jpg`, `image_2.png`,
`image_3.jpg`, `image_4.jpg`) made the complete measurement review available at
244.4 seconds, with zero model-cache hits. Pages streamed in completion order
2, 1, 3, 4 at approximately 84, 112, 218 and 244 seconds; the saved result retained
source order 1, 2, 3, 4. This is run `05b6dbaf31cc`, a set of four different
drawings, not four copies of the single-page comparison input. Background feature
interpretation then took 96.8 seconds and completed without an extraction error.
Combined processing was 341.2 seconds; the polling client observed completion at
342.8 seconds (5 minutes 43 seconds). Complete understanding is therefore still
slower than five minutes on this test set, although measurements are available
earlier. Reducing provider latency further needs additional work and quality
evaluation; the new scheduling alone does not remove it.

Independent pages run two at a time in reading, feature interpretation and BOM
stages. Domo's process-wide in-flight limit remains two. Ordered results preserve
page attribution even when pages finish out of order. Completed page previews
stream before the complete drawing set is ready. Human edits and approval pauses
remain part of the existing workflow.

A controlled four-task scheduling check (0.4 seconds per fake page) took 1.60
seconds sequentially and 0.81 seconds with two workers. This isolates scheduling
overhead; it does not simulate provider latency or prove real drawing speed.

Validation: 186 offline tests passed, including concurrent page attribution,
trace/harness context, failure propagation, role-specific effort and HTTP
progress arriving before completion. JavaScript checks cover arbitrary byte
boundaries, Unicode, out-of-order pages, truncated streams, server errors and
legacy JSON responses. Drawing-review and assembly UI smoke checks also passed.
