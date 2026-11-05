# Raw Goldfish report: zoom_out

**Status:** completed. **Attempt:** 1. **Context:** `fork_turns: none`.
**Persona:** `C:\Users\AkashChandrasekar\.codex\plugins\cache\egm\egm\0.1.0\agents\zoom_out.md`.
**Standards:** none required for BRAINSTORM; the zoom-out standard is REVIEW-only.

## Returned report

The concept addresses a real need, but the evidence supports improving the existing pipeline, not adding agents or replacing its architecture. Under 30 seconds for complete extraction remains unproven.

1. **Blocker — Dimension: Contrarian / YAGNI & Code Minimization. The deadline is a feasibility experiment, not an implementation commitment.**  
   The recorded medium-effort page required 164.5 seconds, approximately 5.5 times the target (`docs/EXTRACTION_PERFORMANCE.md:14–20`). Whole-page reading precedes crop preparation and reading (`app/perception/extract.py:248–272`); feature interpretation then consumes the reconciled dimensions (`app/drawing/planner.py:184–191`). More page workers do not remove these dependencies on one page.  
   **Alternative:** Reuse existing task timing (`app/agent/orchestrator.py:39–52`) to measure the complete critical path on checked detailed pages. Compare a bounded combined extraction-pass experiment against the current independent reads, preserving source evidence and all symbol, section and feature coverage. This is a candidate experiment, not evidence that combining calls can meet 30 seconds. Preserve the current Domo model and effort settings.

2. **Major — Dimension: Contrarian / YAGNI & Code Minimization. Reuse the benchmark, but its current score cannot establish the proposed completeness.**  
   `eval/score.py:9–16` compares normalized dimension tokens. That normalization explicitly removes tolerances and quantities (`app/perception/dims.py:13–17`), so matching `Ø24` cannot prove that `6xØ24`, its tolerance or its association was read correctly. `eval/run.py:7` also warns that drafted truth requires engineering verification.  
   **Alternative:** Extend the existing evaluation with a small, manually checked inventory covering complete callout text, quantities, tolerances, symbols, projection, section references, feature identity and omissions. Keep token recall as a useful diagnostic. Agree the representative pages, quality thresholds and timing percentile before declaring acceptance; one easy part cannot validate the detailed-page claim.

3. **Major — Dimension: Contrarian / YAGNI & Code Minimization. Richer evidence is necessary; a second evidence platform is not.**  
   The current model already preserves page/crop/human readings and explicit corrections (`app/drawing/model.py:23–38, 170–214`). However, reconciliation remains value-based: `combine` groups by normalized token and attaches every matching crop (`app/perception/extract.py:147–169`). Repeated equal values can consequently share attribution; agreement between page and crop is not physical feature identity.  
   **Alternative:** Extend the existing reading records with occurrence-level source locations and associations, retaining raw printed content and unresolved symbols. Reuse `Resolutions`, clarification IDs and source hashes. Avoid parallel stores or a general evidence graph until the acceptance inventory demonstrates a need. Existing feature/view references are useful foundations, not proof that the cited source supports the interpretation.

4. **Major — Dimension: Contrarian / YAGNI & Code Minimization. Reuse extraction results where valid before introducing additional inference.**  
   Background enrichment already produces feature observations (`app/web/server.py:183–206`), while planning invokes interpretation again (`app/agent/orchestrator.py:113–119`). The background pass uses the initial reconciliation; planning may contain human corrections. Blind reuse would therefore compromise correctness.  
   **Alternative:** Reuse completed interpretations only when their source and reviewed model context match; otherwise reinterpret affected evidence. Preserve the immutable enrichment/correction separation documented in `docs/AGENT_WORKFLOW.md:56–57`. This could avoid redundant downstream work, but cannot by itself establish faster initial complete extraction.

5. **Major — Dimension: Contrarian / YAGNI & Code Minimization. Preserve engineering purpose and actual verification through existing feature recipes rather than constructing a new general CAD agent.**  
   The planner already has descriptions, source-bound parameter expressions, derivation reasons and provenance (`app/drawing/planner.py:29–65, 77–137, 253–293`). Existing verification measures native geometry and checks expected volume changes, document identity and body counts (`app/verify/step_verifier.py:92–112, 117–159`). Those checks do not establish every printed dimensional tolerance, sketch constraint or internal feature relationship.  
   **Alternative:** Attach concise engineering intent and approved measurable constraints to existing features; carry that provenance into deterministic recipe geometry and extend the relevant native measurements. Retain the requirement that every line, arc and constraint has a purpose without requiring an independent model decision for each entity. Expand compiler support only for operations required by representative acceptance cases. Its current plate/hole/boss boundary and unsupported blockers are explicit (`app/drawing/planner.py:157–174, 297–312`); recognition of a bore or section does not confer build capability.

**Attestation:** Read the complete Skeptical Architect persona at `C:\Users\AkashChandrasekar\.codex\plugins\cache\egm\egm\0.1.0\agents\zoom_out.md`, complete immutable target `docs/egm/brainstorm-input.md`, and all seven required evidence files in the exact workspace: `docs/EXTRACTION_PERFORMANCE.md`, `docs/AGENT_WORKFLOW.md`, `app/agent/orchestrator.py`, `app/agent/workers.py`, `app/perception/extract.py`, `app/drawing/model.py`, and `app/drawing/planner.py`. Truncated output was reread in chunks. Standards: none required for BRAINSTORM.

**Limitations/status:** Bounded read-only brainstorm completed. No edits, provider calls, native CAD, additional agents, secrets, memory or reviewer reports were accessed. Historical measurements and code inspection do not validate current recognition quality, native geometry correctness or latency acceptance. Those acceptance activities remain incomplete. Existing features, source completeness requirements, effort settings, human review and export gates remain required.
