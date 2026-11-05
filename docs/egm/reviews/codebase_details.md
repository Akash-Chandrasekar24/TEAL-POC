# Raw Goldfish report: codebase_details

**Status:** completed. **Attempt:** 1. **Context:** `fork_turns: none`.
**Persona:** `C:\Users\AkashChandrasekar\.codex\plugins\cache\egm\egm\0.1.0\agents\codebase_details.md`.
**Standards:** none required for BRAINSTORM.

## Returned report

Codebase Details Goldfish, BRAINSTORM: the repository already has useful local tracing, worker timings, immutable readings, correction hashes, cache flags and native before/after checks. The concept should extend these facilities. No new agents or model changes are necessary to add the observability below.

1. **Major — Dimension: Observability & Operational SRE Debuggability. Define one complete-extraction clock and its terminal conditions.**

   Operational risk: the under-30-second target cannot be evaluated consistently using the current timing fields. `app/web/server.py:111–142` measures the initial reading stage. Progressive feature interpretation starts later, potentially waits in `_ENRICH_POOL`, and records a different duration beginning inside the worker at `:170–206`. That duration excludes time waiting for the enrichment worker. Initial `result["seconds"]` is also captured before reconciliation and persistence. A successful background report sets `extraction_status="complete"` after interpretation returns, including when its observations contain unresolved questions (`:197–200`).

   Evidence: the target correctly distinguishes preview from complete extraction at `docs/egm/brainstorm-input.md:27–30, 100–108`; `docs/EXTRACTION_PERFORMANCE.md` records 164.5 seconds combined on the detailed page, versus a browser observation of 166.6 seconds.

   Mitigation requirement: record a single extraction ID and monotonic milestones for accepted input, ingest finished, measurement review available, enrichment enqueued/started, feature and applicable BOM stages finished, final validation finished, and complete result persisted/available. Define whether upload transit belongs in the acceptance clock. Record completion with unresolved evidence separately from successful acceptance. Persist failed, interrupted, unsupported and timed-out terminal outcomes. Measure full extraction latency and its quality result together; retain preview latency as a separate metric.

   Alternative: extend the existing JSON artifacts and trace events and aggregate them offline for the initial benchmark. A distributed monitoring service is unnecessary for the current local process.

   Feasibility limit: no measurements inspected establish under 30 seconds. The existing measurement and feature stages alone substantially exceed it. A chosen corpus, percentile and quality threshold remain prerequisites to an acceptance claim.

2. **Major — Dimension: Observability & Operational SRE Debuggability. Concurrent calls need explicit parent/child correlation and queue timing.**

   Operational risk: run folders preserve broad context, but concurrent identical prompts are difficult to pair reliably. `app/llm/traced.py:34–57` generates a client-local `call_id` yet does not include it in the call, reply or failure event. Independent worker clients have separate counters. Worker task events lack page and invocation IDs (`app/agent/orchestrator.py:39–61`). Prompt hashes repeat across pages. Thus overlapping reads can produce ambiguous start/reply attribution when triaging slow or incorrect extraction.

   Domo timing wraps semaphore wait and HTTP execution together (`app/llm/domo.py:63–82`), so an operator cannot distinguish provider slowness from local contention. `app/parallel.py:6–17` propagates context correctly, which provides a good foundation.

   Mitigation requirement: propagate extraction/result ID, trace ID, parent span or task invocation ID, worker, page, stage, unique call ID, attempt and cache outcome. Add queue-enter/acquire timestamps, provider HTTP duration, parsing/validation duration and response status class. Preserve configured model, returned model and requested effort. Link background traces to their originating extraction explicitly; `result_id` linkage already exists at `app/web/server.py:194–196`.

   Alternative: structured spans in the existing JSONL format suffice locally; adopt an OpenTelemetry exporter only if deployment becomes distributed.

   Feasibility limit: this instrumentation can identify where latency goes. It cannot guarantee faster Domo inference or verify that the provider internally applies the requested effort.

3. **Major — Dimension: Observability & Operational SRE Debuggability. Extraction quality needs actionable evidence metrics rather than counts or agreement.**

   Operational risk: existing summaries can look healthy while important symbols or internal geometry are omitted. `app/perception/extract.py:311–313` reports dimension totals, agreement, single-source and unplaced readings. `app/drawing/planner.py:208` reports feature count and questions. `app/agent/harness.py:91–93` summarizes a `PageRead` as `"extracted"` with dimension/view counts without its issues or projection/section uncertainty. None of these counts establishes completeness against the source.

   The schema retains valuable underlying evidence: projection symbol status, section questions, raw page/crop readings, corrections and source hashes (`app/perception/extract.py:304–314`; `app/drawing/model.py:23–38, 121–137, 157–201`). Recognition and compiler support are already different concepts in the code (`app/drawing/planner.py:140–174, 242–243, 297–312`).

   Mitigation requirement: define benchmark inventory IDs and categories for dimensions, units, tolerances, quantities, symbol families, projection, cutting planes, sections, internal features, notes and BOM content. Record omissions, incorrect readings, incorrect interpretations and unsupported build capability separately. Report category-level recall/precision or agreed equivalents, human correction burden, unresolved items and source-evidence coverage. Link every scored item and correction to a source page/view and extraction version. Treat absent, unreadable, unrecognized, unassessed and unsupported as distinct outcomes.

   Alternative: begin with a human-maintained inventory and an offline scoring report. Runtime model confidence or a second agreeing model can supplement triage but cannot replace the checked inventory.

   Feasibility limit: production telemetry cannot know true omissions without reference annotations or sampled human audits. Current value-level dimension attribution also cannot prove physical feature identity merely because numbers match; the code explicitly warns about that (`app/drawing/model.py:1–4`).

4. **Major — Dimension: Observability & Operational SRE Debuggability. Background extraction needs a diagnosable lifecycle.**

   Operational risk: an extraction can remain `"processing"` without explaining whether it is queued, executing or stalled. The pending artifact contains source hash, boot ID and a processing flag (`app/web/server.py:170–173`). Its attempt ID is created only after client construction and source retrieval (`:187–196`). Exceptions before entering `trace.run` therefore have no associated enrichment trace. Restart interruption is recognized when somebody reads the result (`:231–239`), but there are no persisted stage/heartbeat timestamps in this path.

   Mitigation requirement: persist enqueued time, attempt ID, current stage/page, last progress time, start/finish time and terminal reason from submission onward. Trace setup failures as well as inference failures. Expose queue depth, oldest queued/active age, interruption count and stage failure counts. Supply an operator path from the result ID to the initial run, enrichment artifact, source hashes and relevant traces. Document the existing safe recovery action—re-extraction and review—without bypassing approval gates.

   Alternative: retain the in-process pool and file artifacts for a local application. A durable queue becomes appropriate if automatic restart recovery or multiple server processes enters scope.

   Feasibility limit: process-local state and a boot marker do not provide durable task execution. This review does not assert that a distributed queue is required for the present deployment.

5. **Major — Dimension: Observability & Operational SRE Debuggability. Source-to-plan-to-native-check correlation must accompany the proposed geometry validation.**

   Operational risk: the target requires the engineering purpose and measurable constraints of every operation, including internal geometry (`docs/egm/brainstorm-input.md:78–95`). The compiler currently preserves feature-level observations and parameter provenance (`app/drawing/planner.py:133–137, 289–312`), and native execution already records step verdicts and measurements. However, these are separate reporting layers: native `RunLog` uses a timestamp/name directory (`app/verify/run_log.py:14–30`), while orchestrator summaries expose only bounded status/failure details (`app/agent/harness.py:70–90`). The inspected interfaces do not establish a unified source constraint → sketch entity/feature → native measurement/check chain for the broader proposed geometry.

   Mitigation requirement: assign stable constraint and check IDs and carry source/model/approval hashes, feature ID, native operation/entity references and concise engineering intent into each check result. Record expected and actual values, units, tolerance, measurement method, pass/fail and “not checked” explicitly. Preserve existing volume/body/document checks and include repair/reconciliation attempts. Make an operator able to answer which source requirement failed and which approved constraint produced the operation.

   Existing reuse: `app/verify/step_verifier.py:185–214` already measures before and after actions and examines geometry after a timeout before retrying. `app/verify/run_log.py:35–41` already presents expected/measured volume; `app/agent/live.py:21–32` applies expected-final checks. Extend these mechanisms instead of introducing a second verification log.

   Alternative: one cross-linked verification manifest can index the existing extraction, plan and native logs without merging their storage.

   Feasibility limit: volume, body count and bounding box checks cannot alone prove every internal dimension, profile position or tolerance. Required native measurement APIs and geometry coverage need licensed SolidWorks validation; high CAD effort is not proof.

6. **Major — Dimension: Observability & Operational SRE Debuggability. Preserve enough versioned evidence to reproduce small-symbol failures.**

   Operational risk: current tracing saves input images and raw replies, but text context is logged only as a 200-character prefix and the system prompt as a short hash/description (`app/llm/traced.py:37–57`). The saved image precedes Domo’s possible resize/JPEG conversion (`app/llm/domo.py:101–124`); the transformation warning records dimensions but not the resulting image digest or selected JPEG quality. A later investigation may therefore lack the exact request representation or correction-bearing context used during a failed interpretation.

   Mitigation requirement: record application revision, prompt/schema versions, complete input-content hashes, source/crop transforms, actual transmitted image digest and encoding settings, model/effort/cache settings and immutable model/review hashes. Retain or securely reference the exact approved evidence context needed to reconstruct a request. Expose missing or disabled diagnostic artifacts explicitly.

   Alternative: use a versioned prompt registry plus immutable context artifacts and hashes rather than copying full payloads into every event. Preserve short summaries for routine logs and retrieve detailed artifacts only when investigating.

   Feasibility limit: reproducible request inputs support diagnosis and comparisons; they do not make model responses deterministic.

Attestation: I read the full Codebase Details persona at `C:\Users\AkashChandrasekar\.codex\plugins\cache\egm\egm\0.1.0\agents\codebase_details.md`, the full immutable target `docs/egm/brainstorm-input.md`, and all seven requested evidence files in full: `docs/EXTRACTION_PERFORMANCE.md`, `docs/AGENT_WORKFLOW.md`, `app/agent/orchestrator.py`, `app/agent/workers.py`, `app/perception/extract.py`, `app/drawing/model.py`, and `app/drawing/planner.py`.

Additional evidence read in full: `app/trace.py`, `app/llm/traced.py`, `app/llm/domo.py`, `app/parallel.py`, `app/agent/harness.py`, `app/verify/run_log.py`, and `app/agent/live.py`. Additional partial/search evidence: `app/web/server.py:1–295`, `app/verify/step_verifier.py:166–215` plus search matches, and search matches in `app/runner/runner.py` and `app/llm/base.py`.

Standards: none required for BRAINSTORM. Limitations: static, read-only review; no provider calls, native CAD execution, benchmark execution, secrets, stored conversations or other reviewers’ reports accessed; no files changed. Current Domo/model constraints, medium extraction effort, high CAD effort and existing review/export gates are preserved. Status: completed.
