# Extraction speed enhancements, 2026-11-05

The current Domo model is unchanged (`domo.openai.gpt-5.6-sol`). Extraction
remains medium; SolidWorks remains high. The user explicitly chose to continue
optimizing this model instead of evaluating other models.

## Enabled infrastructure changes

- A hidden persistent Windows OCR helper reuses the recognition engine.
  Requests are serialized, bounded by a timeout including queue time, and the
  helper is restarted after a crash, protocol failure or timeout. A stale
  response cannot be assigned to a new request. Shutdown closes the helper.
- Idle server startup recognizes a blank tile, initializes the evidence schema
  and constructs the Domo transport before an upload. It makes no model call
  and precomputes no drawing result. The server listens while warming proceeds.
- Native-size RGB PNGs go directly to OCR without decoding and encoding the
  same pixels again. Other image modes and oversized images retain conversion
  and source-coordinate scaling.
- Domo connections are reused across clients, isolated by endpoint, credential
  digest and timeout. Model and reasoning effort remain request-specific. The
  in-flight limit, evidence validation, cache keys and approval gates remain.
- Known section-reference objects containing only a label and evidence can be
  unpacked into the existing fields. Both evidence strings survive. Unknown
  structures still fail validation; no extra provider call is needed to unpack
  an equivalent representation.
- The benchmark now supports repeats, explicit startup initialization and
  bounded parallel page processing matching the application. It preserves
  failures and separates total provider-call durations from whole-input time.

On one native OCR reuse check, the same dense image took **2.678 s** on cold
startup and **1.089 s** on a subsequent recognition, without a result cache.
After blank-tile startup and direct-PNG changes, fresh recognition took:

| Input | OCR seconds | OCR lines |
| --- | ---: | ---: |
| Synthetic plate | 0.303 | 10 |
| Support block | 0.259 | 17 |
| Dense triple clamp | 0.466 | 25 |

These are individual observations, not latency percentiles. Native OCR remains
fallible: additional rotated-image probes did not reliably recover the dense
sheet's difficult diameter callouts and were not enabled in production.
A final parity check compared source line IDs, full text and positioned boxes
against the original OCR snapshots for all three images: all were identical.
Those fresh checks took 0.341, 0.255 and 0.740 seconds respectively and are saved
in `eval/runs/native-ocr-speed-check.json`.

## Uncached extraction experiments

Every live trial used the current Domo model at medium effort and no model
answer cache. Whole-input time includes ingestion, native OCR, model work,
validation, crop publication and evidence integrity verification. Warm trials
exclude explicitly completed idle initialization. Repeated calls still perform
fresh OCR and fresh provider inference. These are extraction/evidence tests,
not native CAD builds.

| Candidate/input | Whole-input seconds | Exact measurement texts | Outcome |
| --- | ---: | --- | --- |
| Compact tuple transport, support block | 61.512 | 19/19 | Slower than earlier R1 |
| Compact tuple transport, dense | 77.933 | Not scored | Failed: quantities serialized as strings |
| OCR anchors, support block | 54.558 | Replay: 19/19 | Failed format detection; parser subsequently fixed |
| OCR anchors, synthetic | 38.386 | Replay: 6/6 | Same format-marker failure |
| OCR anchors, corrected, synthetic repeat 1 | 61.579 | 6/6 | Over budget |
| Same, repeat 2 | 41.150 | 6/6 | Over budget |
| Same, repeat 3 | 49.173 | 6/6 | Over budget |
| Short standard-schema prompt, synthetic, before fuller warmup | 33.568 | 6/6 | Over budget |
| Short prompt, warm synthetic repeat 1 | 24.099 | 6/6 | Under 30 s, not consistent |
| Same, repeat 2 | 32.810 | 6/6 | Over budget |
| Same, repeat 3 | 30.924 | Not scored | Failed section-reference shape; parser subsequently fixed |
| Short prompt, support block | 46.503 | 19/19 | Over budget |
| Parallel readings/features, synthetic | 29.574 | 6/6 | Under 30 s on this observation |
| Parallel readings/features, support block | 61.289 | 19/19 | Slower; not promoted |
| Parallel readings/features, dense | 75.926 | 50/53 | Important omissions/wrong readings |
| Unchanged verbose R1, warm synthetic repeat 1 | 31.136 | 6/6 | Over budget |
| Same, repeat 2 | 30.088 | 6/6 | Over budget, not rounded down to success |
| Unchanged verbose R1, warm support block | 39.476 | 19/19 | Over 30 s |
| Short prompt, dense, sequential final check | 81.955 | 50/53 | Over 40 s; worse than earlier R1 |
| Restarted server, unchanged verbose R1, controlled synthetic HTTP upload | 29.587 | 6/6 | Fresh provider call, under 30 s on this observation |

The final HTTP check includes file reading/base64 preparation, upload, model
work, retained evidence, response transfer and parsing against the running
server. Server extraction time was 29.434 s. It used one provider call and zero
model-cache hits, retained both views, printed units/projection and section A-A,
and kept CAD readiness false. The result is reviewable at
`http://127.0.0.1:8765/#8ed86bde33e1`; its report is
`eval/runs/server-r1-speed-check.json`. This is a controlled drawing, not one of
the dense real drawings or a certified reconstruction.

The four checks in `final-speed-checks` ran sequentially. Some earlier jobs overlapped; in
particular the parallel support/dense jobs briefly had four provider calls in
flight across separate benchmark processes. Their timings do not establish
isolated-load performance. The application still limits Domo to two calls in
flight per process. An OCR process and connection pool are also per process.

Parser replays are offline validation/scoring of saved responses, not successful
fresh inference trials or new timing measurements. Original failure reports are
retained. The compact tuple prototype has been superseded by the experimental
OCR-anchor transport; its prompt fingerprint, input image and raw reply remain
in the trial trace.

## Quality and deployment decision

The synthetic successes retained complete quantities/tolerances, both views,
printed units/projection text, section A-A, and questions for the missing hole
locations. They do not certify full engineering accuracy. Real references are
assistant visual transcriptions, not independently engineer-verified inventories.

The dense shorter-prompt and parallel candidates still misread `Ø155`, `Ø135`
and an angular callout. Section-label and local-note checks also regressed in
some candidates. Fewer output tokens did not reliably reduce provider reasoning
time. Parallel analysis sometimes produced more extensive feature reasoning and
more uncertain attachments. These candidates are **not deployed as defaults**.

The application retains its existing reviewed workflow, and verbose R1/R2 remain
the evaluation defaults. New transports are explicit benchmark options only.
Experimental feature surveys preserve original positions and printed reference
texts. A feature reference binds only when both full text and source location
resolve uniquely; ambiguous/unmatched references remain questions. Notes and
symbols may support features such as through termination or section identity.
Raw survey evidence is retained. No candidate bypasses human acceptance or CAD
export gates.

**A reliable under-30-second result has not been achieved. The 40-second dense
page allowance has not been achieved either.** Infrastructure overhead is now
small, but medium-effort Domo inference still varies substantially. Individual
24.099 s, 29.574 s and 29.587 s successes must not be presented as a general SLA.

Validation: 214 offline tests passed. Live OCR parity checks passed on all three
images. The restarted server returned HTTP 200 for configuration and extraction;
its error log was empty. Default prompts, model, worker efforts, review gates
and the existing reviewed CAD workflow remain intact.

## Artifacts and reproduction

All reports, including failures, are under `eval/runs/` in:
`compact-dense-v1`, `compact-support-v1`, `anchor-support-v2`,
`anchor-synthetic-v2`, `anchor-synthetic-v3`, `lean-synthetic-v1`,
`lean-synthetic-warm-v2`, `lean-support-v1`, `split-synthetic-v1`,
`split-support-v1`, `split-dense-v1`, and `final-speed-checks`.
Each completed trial retains source hashes, original view crops, model traces,
OCR evidence, exact-string scores and immutable S2 artifacts.

```powershell
.venv\Scripts\python.exe eval/compare_extraction.py eval/files/hybrid_test_plate.png --strategies r1 --transport verbose --reference eval/files/hybrid_test_plate_reference.json --budget 30 --warm-ocr --repeat 3
.venv\Scripts\python.exe eval/compare_extraction.py inputs/image_11_triple_clamp.png --strategies r1 --transport lean --reference eval/hybrid_triple_clamp_reference.json --budget 40 --warm-ocr
```

Use `--transport anchored` or `--transport split` for the other explicit
experiments. R2 retains its verbose baseline. Run benchmark jobs sequentially
for a controlled provider-load comparison.

## 2026-11-10: tiled reading tried and rejected (Domo queues parallel calls)

Tried: each page as a layout call + a feature survey + one call per tile (6 tiles on an A3 page at
300 DPI, so near full resolution), all at the same time, then merged. Water Pump PDF (3 pages, 24 calls):
after 7 minutes it had not finished (the whole-page read takes about 3.5 minutes). Single small tile
replies (about 2k output tokens) took 50 s when sent first and 200-310 s once many calls were in
flight: Domo answers roughly one call every 15-30 s however many are sent, so what matters is the
total tokens per page, and tiling added tokens (every tile spends about 2k tokens thinking).
Removed. Correction (same day): the queue was mostly this app's own limit, DOMO_MAX_IN_FLIGHT=2 (set because about 6
calls in flight stalled Domo for 10 minutes on 2026-11-03), so 24 calls ran 2 at a time. Either way tiling cannot win on Domo:
more calls than about 2-4 at once are not served, and tiling adds tokens. For the same reason PAGE_WORKERS above 2 gains nothing
while DOMO_MAX_IN_FLIGHT is 2.
Also kept: PDF pages saved with PNG compress_level 6 instead of optimize (about 2.5 s a page faster).

## 2026-11-10: three Domo calls at once

Water Pump PDF, 3 pages, verbose R1, GPT-5.6 Sol, all three page calls sent together (DOMO_MAX_IN_FLIGHT=3):
pages took 91.5 s, 100.7 s and 79.1 s, so the whole PDF took 100.7 s, against 212 s with the old cap of 2 (page 3 had waited
for a free slot). Each call was as fast as with fewer calls in flight. The default is now 3; 4 or more is untested (about 6 stalled).

Other models on this Domo instance, same page (Wankel page 1, 76 reviewed measurements), same prompt, medium effort:
GPT-5.6 Sol 73 s, 76/76. Gemini 3.7 Flash 25-29 s but found 16 of 76 (it skips most of the sheet). Gemini 3.5 Flash 23 s, 5 found.
GPT-5.4 mini 153 s and DomoGPT Medium v3 150 s, both slower (and returned nulls the schema rejects). Sol stays.
