# Raw Goldfish report: design_critic

**Status:** completed. **Attempt:** 1. **Context:** `fork_turns: none`.
**Persona:** `C:\Users\AkashChandrasekar\.codex\plugins\cache\egm\egm\0.1.0\agents\design_critic.md`.
**Standards:** none required for BRAINSTORM; REVIEW-only standards not applied.

## Returned report

Persona: Design Critic Goldfish, BRAINSTORM. The concept is suitable for a feasibility experiment, but the evidence does not support committing to complete extraction under 30 seconds with the current Domo model and medium effort.

1. **Blocker to a performance commitment — Dimension: Cost, Quota & Token Economics; architectural critical path.**

   The measured medium-effort result is 98.8 seconds for measurements plus 65.7 seconds for feature interpretation, totaling 164.5 seconds (`docs/EXTRACTION_PERFORMANCE.md:12–27`). Reaching 30 seconds requires approximately a 5.5-fold improvement on that observed case while adding confidence in small symbols and internal sections.

   The implementation has real dependencies: the whole-page response supplies agent-selected view boundaries (`app/perception/extract.py:244–265`), crop readings follow (`269–302`), and feature interpretation receives the reconciled drawing and source image (`app/drawing/planner.py:178–210`). Multiple crop sheets add sequential calls. Parallelizing different pages does not remove these single-page dependencies.

   **Mitigation requirement:** Treat 30 seconds as an unproven experiment target. Define the checked drawings, completion criteria, latency percentile, cold-cache conditions and concurrency level before evaluating it. Record rendering, packing, queue waiting, each provider call, reconciliation and complete feature/BOM extraction separately. Preserve extraction medium effort and CAD high effort.

   **Alternatives:** Experiment with a combined structured reading or parallel whole-page/crop reading when independently available boundaries permit it, then compare omissions and interpretation against the existing approach. Neither alternative currently proves the deadline; model changes remain unauthorized and unresolved.

2. **Major — Dimension: Cost, Quota & Token Economics; shared provider capacity.**

   All Domo operations share a process-local semaphore (`app/llm/domo.py:30–31,78–82`). The recorded configuration permits two concurrent requests (`docs/AGENT_WORKFLOW.md:50–52`). Extraction, BOM enrichment and CAD planning therefore compete for the same scarce capacity. Increasing application processes multiplies permitted requests because each process creates its own semaphore.

   Two in-flight requests are a concurrency limit, not a requests-per-second quota. Sustainable throughput cannot be established without observed request service times and the account’s actual request/token limits. Adding page workers or agents can increase waiting and quota pressure without improving completion time.

   **Mitigation requirement:** Establish one provider-wide admission policy with bounded queue depth, fair scheduling between foreground extraction, enrichment and CAD, explicit queue-wait telemetry and aggregate request/token accounting. Before multiple application processes are introduced, move admission into a shared coordinator. Define peak simultaneous drawings and maximum pages per drawing.

   **Alternative:** Retain a single application process and the current concurrency limit for an initial deployment with explicit load limits. This bounds scope; it does not establish a 30-second guarantee.

3. **Major — Dimension: Cost, Quota & Token Economics; budgets do not currently bound complete-drawing expenditure.**

   The harness defaults allow 64 calls, one million tokens and 7,200 model seconds (`app/agent/harness.py:33–54`). Token and time checks inspect consumption before a request, then charge actual usage afterward (`212–237`); concurrent calls can both pass before either reports usage. These are useful safety limits, but they do not reserve the remaining budget or interrupt an already-running call.

   Progressive enrichment constructs another orchestrator and harness (`app/web/server.py:183–200`), while initial extraction saves its own harness snapshot (`165`). These paths do not demonstrate one cumulative budget covering upload, enrichment, correction, planning and retries. Browser token totals also omit reasoning tokens (`64–82,139–142`), although Domo exposes them (`app/llm/domo.py:71–73`).

   **Mitigation requirement:** Define a persistent budget per drawing across stages and attempts, including reasoning usage, cache hits, failed calls and retries. Reserve estimated usage before concurrent dispatch, reconcile against actual usage afterward, and specify what happens when provider usage is unavailable. Distinguish an operational cap from an enforceable billing cap.

   **Alternative:** Begin with per-drawing call ceilings and conservative size limits, then tighten token/cost reservations using measured usage. Actual monetary estimates require verified Domo billing information; none was provided or retrieved.

4. **Major — Dimension: Cost, Quota & Token Economics; image resolution creates a quality–call-count tradeoff.**

   Whole-page images are reduced to a maximum side of 2,048 pixels; oversized payloads can become JPEG (`app/llm/domo.py:101–124`). Packed crops may be reduced to 60%, and an individual oversized crop can be reduced further to fit (`app/perception/extract.py:46–48,94–105`). Whole-page dimensions are therefore not a reliable safety net for detail lost in both representations.

   Full source-backed understanding of tiny GD&T frames, projection symbols and section callouts can require additional local-resolution crops. Those calls directly increase provider occupancy, tokens and latency.

   **Mitigation requirement:** Preserve original sources and crop transforms; measure the transmitted resolution of the smallest relevant markings. Add explicit readability/coverage failures and a bounded targeted-crop path. Evaluate symbol and section recognition separately from numeric recall. Never silently discard fine detail to satisfy the deadline.

   **Alternative:** Use a coarse inventory followed by targeted detail reads. This can allocate calls more efficiently than uniform tiling, but it risks missing details absent from the inventory and needs a human-checked completeness evaluation.

5. **Major — Dimension: Cost, Quota & Token Economics; current evidence identity can amplify review and context costs.**

   Dimensions are combined by normalized token and assigned all views containing that token (`app/perception/extract.py:147–169`). Reconciliation then attaches every page/crop reading containing that token to each resulting dimension (`app/drawing/model.py:188–201`). Equal-valued dimensions can consequently carry overlapping evidence without a precise identity for each printed occurrence. Repeated values can also duplicate substantial context.

   The current schema has dimension text and view provenance, but no general typed observation structure for independently located GD&T frames, datums, weld symbols, surface finish, quantities, tolerances and their attachments (`app/drawing/model.py:23–38,61–77`; `app/drawing/planner.py:29–39`). Broadening prompts alone will not provide the complete auditable evidence structure requested by the concept.

   **Mitigation requirement:** Define stable source-occurrence identities, page coordinates or equivalent locators, raw text/symbol evidence and explicit links from occurrences to interpreted features. Keep recognition, interpretation and build support separate. Store shared source observations once and reference them, while preserving existing saved-reading compatibility and corrections.

   **Alternative:** Add an observation layer beside the existing dimension model, then progressively migrate consumers. Replacing existing schemas wholesale creates unnecessary compatibility risk.

6. **Major — Dimension: Cost, Quota & Token Economics; repeated feature interpretation can consume the savings.**

   Initial workflow extraction already performs feature interpretation (`app/web/server.py:153–159`), and progressive extraction does so in enrichment (`197`). Nevertheless, `Orchestrator.plan` unconditionally interprets again when the model has no unresolved issues (`app/agent/orchestrator.py:113–119`). This can repeat an expensive stage and generate different observations for the same source.

   Reinterpretation may be necessary after a human correction, but the present handoff does not demonstrate reuse of a validated interpretation tied to the corrected drawing digest.

   **Mitigation requirement:** Make interpretation artifacts immutable and keyed by source hash, reviewed-model digest, model/effort, prompt/schema version and any relevant human clarification. Reuse only matching validated artifacts; invalidate affected observations after corrections. Account for repeated interpretation in latency and cost reports.

   **Alternative:** Continue recomputing interpretation for correctness initially, but explicitly budget and disclose that cost. Do not count early background extraction as eliminating downstream interpretation expenditure.

7. **Major — Dimension: Cost, Quota & Token Economics; background execution and memory/storage need bounded ownership.**

   Progressive extraction is submitted to a process-local two-thread executor after saving a pending record (`app/web/server.py:53–54,168–174`). The inspected path does not establish durable queued execution after process loss. Its timer starts when the worker begins (`183–185`), so enrichment duration excludes time waiting in the executor queue.

   Upload limits concern six files and request bytes (`57–58,95–96`), while `load_pages` materializes rendered pages before processing (`113–138`). These inspected paths do not establish limits on PDF page count or total decoded pixels. Source images, uploads, traces and enrichment artifacts occupy separate storage areas (`44–52`); a cache capacity setting alone cannot establish total retention cost.

   **Mitigation requirement:** Define persistent extraction states and restart recovery, include queue wait in end-to-end timing, bound pending work and total rendered pixels/pages, and specify retention for every artifact class. Distinguish “processing finished” from “complete evidence” and “human verified.”

   **Alternative:** Keep local execution for a bounded single-user release, with explicit interrupted-job recovery and admission limits. A durable queue becomes necessary when unattended completion or process scaling is promised.

8. **Major — Dimension: Cost, Quota & Token Economics; downstream CAD remains a separate capacity and verification boundary.**

   Workers run in one process and CAD execution is deliberately serialized (`docs/AGENT_WORKFLOW.md:100–105`). Current compilation supports a rectangular plate, plain holes and circular bosses, blocking unsupported geometry (`app/drawing/planner.py:240–312`). Relationship references are checked (`236–238`), but this function does not establish that those relationships were imposed or measured in SolidWorks. Parameter provenance and nominal arithmetic are not complete native geometric verification.

   **Mitigation requirement:** Preserve execution locking, human approval, unsupported-feature blockers and final export confirmation. For each added capability, define engineering intent, deterministic preconditions, expected geometric changes, native measurements and tolerance comparisons. Record CAD queue wait and license/session occupancy separately from the extraction target. Validate internal geometry using the approved section evidence.

   **Alternative:** Prove a representative supported part first, then expand compiler coverage incrementally. This remains unsuitable as evidence of arbitrary assembly or section-feature build capability.

**Feasibility limits:** No live provider calls, load tests, cost measurements, native CAD runs or recognition evaluations were performed. The historical timing is evidence of a substantial gap, not proof that every possible redesign is infeasible. Under-30-second complete extraction with the current model and medium effort remains unestablished. Account quotas, pricing, acceptance drawings, accuracy thresholds and timing percentile remain open. No additional agents or model changes are required by these recommendations.

**Attestation:** I read the full Design Critic persona at `C:\Users\AkashChandrasekar\.codex\plugins\cache\egm\egm\0.1.0\agents\design_critic.md`; the full immutable target `docs/egm/brainstorm-input.md`; and all selected evidence in full: `docs/EXTRACTION_PERFORMANCE.md`, `docs/AGENT_WORKFLOW.md`, `app/agent/orchestrator.py`, `app/agent/workers.py`, `app/perception/extract.py`, `app/drawing/model.py`, and `app/drawing/planner.py`. Additional evidence read: full `app/llm/domo.py` and `app/parallel.py`; `app/agent/harness.py:25–65,200–260`; `app/web/server.py:40–230`; and matching configuration/cache search lines. No secrets, conversations, memory or other reviewer reports were accessed. No files were changed, artifacts written, providers called, CAD executed or agents spawned. Standards: none required for BRAINSTORM; REVIEW-only standards were not applied. Status: completed.
