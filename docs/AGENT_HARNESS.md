# Bounded ReAct harness

The existing LangGraph already provided bounded rereads, transient-provider
retries, feature repairs, human pauses and persistent jobs. The application now
also has an explicit runtime harness around both workers.

This is a **constrained ReAct-style workflow** implemented with the existing
LangGraph state machine. It does not install a general autonomous ReAct package
or let a model choose arbitrary tools. The model proposes a construction plan or
parameter repair with a short reason; code validates it, a human approves it,
code acts in SolidWorks, and measured results return as observations for the next
repair decision. The existing one-call drawing-to-list path remains available.

The [original ReAct work](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/)
interleaves model reasoning and actions with environmental feedback. Here the
action vocabulary and human approval gates intentionally bound that pattern.
The UI remains the existing HTML/JavaScript interface; this is unrelated to React.

## Runtime controls

| Control | Enforcement |
|---|---|
| Role ownership | Extraction can read pages, interpret visual evidence, read BOMs and extract legacy lists. Only the SolidWorks worker can plan, propose repairs or invoke builds. Unknown worker/action pairs are refused before dispatch. |
| Existing CAD contract | Original menu schemas, provenance compiler, approved-list hashes, parameter-only repair guards and runtime geometry checks remain authoritative. |
| Model budgets | Every worker `generate_json` attempt, including provider failures and cache lookups, consumes an attempt. Reported prompt/output/reasoning counters and active call time are accumulated. |
| Persistent limits | Part-job budgets and observations are checkpointed in `harness`. Resuming or recreating a graph retains the saved job's policy and usage. Jobs sharing a compiled graph have separate ledgers. |
| Browser limits | One supervisor shares its ledger across all pages or interpretation/planning stages of a request. Saved read/plan/BOM artifacts retain the ledger. A new explicit browser attempt has its own budget. |
| Repeated repairs | The same proposed parameters for the same approved plan and failed item may appear twice. A third identical proposal stops for human review, in addition to the existing per-item repair limit. |
| Bounded context | Only the latest six compact observations/evaluations enter the repair package. Public decision history is capped at 64 records. Drawing pixels, prompts and raw model replies are not copied into that history. |
| Handoff checks | Preapproved/invalid planner results and incomplete native reports stop the workflow. Completing a model call is recorded as returning data, not proof of valid geometry. |
| Native writer lease | The production part and assembly entrypoints share `storage/solidworks-execution.lock` within this project. A second writer is refused before it starts/uses the CAD runtime. Existing assembly CLI locking is retained. |
| Native intent | `harness/native-intent.json` is written under the job before native side effects, with process, operation, plan hash and start time. An interrupted/uncertain action requires reconciliation before retry. |

Limits are cooperative boundaries checked **before the next model call**. A
response can exceed the remaining token/time allowance; the next call is blocked.
Token totals conservatively sum the usage fields reported by the provider and
are not an exact billing calculation. Human waiting time is excluded. The
existing provider request deadline still applies. The harness never kills a CAD
operation in the middle of a native feature.

## Configuration

Both workers use Domo by default. Extraction uses
`EXTRACTION_REASONING_EFFORT=medium`; CAD planning and repair use
`CAD_REASONING_EFFORT=high`. Both are reported by `/api/harness`.
Optional limits in `.env`:

```dotenv
HARNESS_MAX_MODEL_CALLS=64
HARNESS_MAX_TOKENS=1000000
HARNESS_MAX_MODEL_SECONDS=7200
HARNESS_MAX_ACTIONS=128
```

These defaults accommodate the current multipage flows. Adjust them using real
workload measurements; no geometric accuracy or speed improvement is implied by
the limits. A checkpoint retains the policy recorded for that job. New jobs use
the current settings. Invalid/nonpositive settings are refused.

The harness does not introduce another automatic retry layer. The existing graph
still owns its one transient-provider retry, format retry, read limits and repair
limits. The native part runner retains its existing classified recovery. A
harness boundary in a part job ends at `needs_human`, rather than repeatedly
calling a model or suggesting a geometric fix for a runtime/budget problem.

## ReAct trajectory and monitoring

The Process log includes `harness` events for:

1. **Decision:** the selected supported operation, or a validated repair's short
   reason awaiting approval.
2. **Action:** dispatch to the assigned worker.
3. **Observation:** returned status, dimension/view counts, unresolved-question
   counts or a compact measured failure (item, kind, code and message).
4. **Evaluation:** the existing validation, approval, repair or error boundary.

These are public task summaries and measured outcomes, not private model
reasoning. Raw images/replies remain in the existing separately controlled trace
files. Harness history is a bounded working context, not a complete audit archive.

`GET /api/harness` reports the action registry, configured policy and gate/retry
ownership without credentials. Browser artifacts contain their saved `harness`.
`run_job.py status --job ID` shows model attempts, reported tokens and actions.
Older checkpoints without harness data start with an empty ledger; prior usage
cannot be reconstructed reliably from old checkpoints.

## Native recovery limits

An interrupted process may leave the shared lease and a `running` intent. The
harness does not steal stale locks or automatically replay an uncertain action.
Inspect the owning job, output report and current SolidWorks documents first.
After explicit reconciliation, an operator can archive the stale lock/intent
and start a reviewed retry. This increment provides no automatic reconciliation
command. Exceptions during native execution mark the intent `needs_review`.
Normal returns (including checked failure reports) complete the intent and retain
the existing explicit retry behavior.

This does **not** guarantee exactly-once CAD side effects across every crash
window, serialize outside programs or recover native CAD state from a Python
checkpoint. A completed native call followed by a checkpoint failure still needs
operational care. Native geometry cannot be certified by simulated outputs.

## Validation

```powershell
.venv\Scripts\python.exe tests/run_all.py
.venv\Scripts\python.exe tests/check_drawing_ui.py
node tests/check_assembly_ui.cjs
```

Harness tests exercise cross-role calls, action/model/token/time limits,
checkpoint resumption, independent jobs, repeated repairs, measured-feedback
context, invalid handoffs/usage, persisted browser ledgers, concurrent native
writers, stale locks and uncertain side effects. Existing tests continue to
exercise approvals, repair scope, geometry validators and browser behavior.
Live Domo interpretation quality and licensed SolidWorks fidelity still require
engineer-reviewed drawings and real reference CAD acceptance cases.
