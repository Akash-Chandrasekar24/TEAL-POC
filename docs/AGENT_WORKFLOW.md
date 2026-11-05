# Orchestrator and two workers

The application assigns its existing operations to two specialist workers under
one deterministic orchestrator. A bounded ReAct harness now enforces action
ownership, persistent budgets and measured feedback, without adding native CAD
capabilities or extra inference stages. See [Agent harness](AGENT_HARNESS.md) for
limits, recovery, observability and the precise scope of the ReAct loop.

| Role | Existing operations it owns |
|---|---|
| ExtractionAgent | Whole-page/crop dimension reading, visual feature observations, whole-sheet BOM extraction, legacy feature-list extraction |
| SolidWorksAgent | Cross-view text planning and deterministic compilation, repair proposals, existing part and assembly execution |
| Orchestrator | Dispatch, task timing/outcomes, worker status, existing LangGraph routing and human approval/review gates |

The worker classes are in `app/agent/workers.py`; dispatch and trace monitoring
are in `app/agent/orchestrator.py`. The persistent part-job graph remains in
`app/agent/graph.py`. Browser drawing reads, browser planning/BOM reads, the views
CLI, part jobs and the assembly execution CLI use these boundaries. Low-level
extraction, planning and CAD APIs remain available with their existing signatures.

## Domo

Both workers use independent clients for the same `DOMO_MODEL`, with the existing
cache/tracing wrappers and process-wide Domo concurrency limit. Set:

```dotenv
LLM_PROVIDER=domo
WORKER_LLM_PROVIDER=domo
DOMO_MODEL=domo.openai.gpt-5.6-sol
DOMO_REASONING_EFFORT=high
EXTRACTION_REASONING_EFFORT=medium
CAD_REASONING_EFFORT=high
```

`WORKER_LLM_PROVIDER` defaults to Domo independently of an inherited legacy
`LLM_PROVIDER` environment setting. CLI and browser explicit selections still
override it. Both workers share the existing `DOMO_MODEL` setting.

Extraction requests **medium reasoning effort** for drawing readings, features,
readiness and BOM evidence. CAD planning and repair request **high effort**.
The role-specific settings above override the generic `DOMO_REASONING_EFFORT`
fallback. The request uses Domo's `reasoningConfig.reasoningEffort` field as defined in its
[official AI Services schema](https://github.com/DomoApps/domo-documentation-hub/blob/main/openapi/product/AI-Services.yaml).
Effort is included in cache keys and model-call logs, so lower/default-effort
cached answers are not reused for high-effort calls. The deterministic
orchestrator and native CAD execution have no separate model reasoning setting.
This configures the requested effort; offline tests do not verify how a live
Domo model applies it.

Page reads, feature interpretation and BOM reads use bounded concurrency (two
pages per stage). Results retain their source page order and each thread inherits
the run trace and harness budget. The process-wide Domo request limit remains two.
Browser uploads request newline-delimited progress events: `started`, `page`,
then `result` or `error`. Completed page previews appear before the whole set is
ready. The existing JSON endpoint remains available when `stream` is omitted.
With progressive extraction, immutable initial readings are saved before feature
and BOM interpretation; separate enrichment files never overwrite human edits.

Keep `DOMO_INSTANCE` and `DOMO_DEVELOPER_TOKEN` in your local `.env`. The local
provider settings have been switched to Domo; credentials are retained. No key is
needed by the orchestrator itself. Execution of an already approved assembly
does not construct either model client or make model calls.

Explicit Gemini overrides remain supported. Existing saved part jobs retain the
provider recorded in `meta.json`, and existing browser results retain their saved
provider unless explicitly overridden. New work uses Domo by default. Restart
the local server after changing `.env`.

## Preserved behavior

- Page rendering, segmentation, extraction prompts and schemas are unchanged.
- The browser planner still performs one visual interpretation per page followed
  by one text planning call. Interpretation belongs to extraction; the text plan
  belongs to SolidWorks. Combined call/token totals still cover both workers.
- The legacy `run_job.py start --image ...` path retains its one-call feature-list
  extraction format. It has not been replaced with the narrower browser compiler.
  That draft remains unapproved; it still passes the same validation and human
  approval before execution. Separating its extraction and planning into new model
  calls is a future enhancement, not part of this assignment-only increment.
- Starting from a saved list still skips reading. Old graph node names and
  checkpoint fields remain valid; `worker_status` is additive.
- List hashing, parameter-only repair guards, retry/read/repair limits, cache
  invalidation and human final review remain in the existing implementations.
- Part execution still uses the same runner, feature recipes, verification,
  exports and SolidWorks session. Assembly execution retains explicit opt-in,
  persistent approval, execution locking, native checks and final human review.
- The orchestrator does not add an LLM decision before an approval or a build.

## Monitoring

The existing Process log includes `orchestrator` / `worker task start`, `done`,
and `failed` events with worker, task, provider/model and elapsed time. Completion
of a model operation is not geometric acceptance; its result still passes the
same downstream validation. Errors are propagated to the existing handlers.

Part jobs additionally checkpoint the last task and resulting workflow status for
each worker in `worker_status`, shown by `run_job.py status`. Human pauses keep
their original payloads and remain replay-safe. Assembly execution traces are
saved under the assembly job's `traces/<attempt>/`; BOM reads use `storage/runs/`.
Workers run in the same application process, and CAD execution is not parallelized.

The harness now saves bounded action/observation history and usage budgets with
part-job checkpoints and browser artifacts. Native part and assembly entrypoints
share an exclusive writer lease, and uncertain executions require reconciliation
before retrying. These controls augment the original gates and limits.

## Verification

The whole-page reader records `title_block.projection_symbol` with a presence
status, first/third-angle convention and visible evidence. It uses printed text
or a readable symbol, not country or view layout. Unreadable symbols or conflicts
with printed projection text require human confirmation. Older saved readings
remain readable; re-extract them to obtain this additional evidence.

Section views retain their captions, cutting-plane references, internal-geometry
evidence and questions. Questions join the source-review flow. Feature
interpretation uses sections for bores, cavities, shoulders, depths and wall
thickness, while hatching represents cut material. The CAD worker receives these
view readings together with the existing feature observations and dimension IDs.
This adds extraction evidence; it does not expand the compiler's supported
geometry. Unsupported features remain explicit review blockers.

```powershell
.venv\Scripts\python.exe tests/run_all.py
.venv\Scripts\python.exe tests/check_drawing_ui.py
node tests/check_assembly_ui.cjs
```

Tests cover role isolation, separate Domo clients, legacy job behavior and resumed
checkpoints, approval and repair boundaries, unchanged planning output/call
counts, per-worker invalid-cache removal, error monitoring and assembly routing.
Live Domo interpretation quality and licensed SolidWorks geometry still require
their separate acceptance tests.
