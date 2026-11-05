"""Assign existing operations to two workers and monitor their handoffs.

The LangGraph in graph.py remains the persistent part-job state machine. This
supervisor also serves the browser and assembly CLI, without adding model calls,
changing approvals, or introducing a second execution/retry implementation.
"""
from __future__ import annotations

import time
from contextlib import contextmanager

from app import trace

from .workers import ExtractionAgent, SolidWorksAgent
from .harness import AgentHarness, HarnessStopped, current_harness, observation


class Orchestrator:
    def __init__(self, extraction: ExtractionAgent | None = None, solidworks: SolidWorksAgent | None = None,
                 *, harness: AgentHarness | None = None):
        self.extraction = extraction if extraction is not None else ExtractionAgent()
        self.solidworks = solidworks if solidworks is not None else SolidWorksAgent()
        self._harness = harness if harness is not None else AgentHarness()

    @property
    def harness(self):
        return current_harness() or self._harness

    @classmethod
    def with_provider(cls, provider=None, *, cache=None, build=None):
        """Separate worker clients; both use the selected provider/model.

        New work defaults to Domo. Explicit provider overrides and saved job
        provider selections continue to work for compatibility.
        """
        return cls(ExtractionAgent(provider=provider, cache=cache),
                   SolidWorksAgent(provider=provider, cache=cache, build=build))

    @contextmanager
    def _task(self, role, task):
        worker = self.extraction if role == "extraction" else self.solidworks
        metadata = {"worker": role, "task": task, **worker.identity()}
        tr, started, result = trace.current(), time.monotonic(), {}
        tr.event("orchestrator", "worker task start", **metadata)
        try:
            yield result
        except Exception as ex:
            tr.event("orchestrator", "worker task failed", level="error", **metadata,
                     seconds=round(time.monotonic() - started, 3), error=f"{type(ex).__name__}: {ex}")
            raise
        tr.event("orchestrator", "worker task done", **metadata,
                 seconds=round(time.monotonic() - started, 3), **result)

    def _dispatch(self, role, task, *args):
        with self.harness.session(), self.harness.action(role, task) as summary, self._task(role, task) as event:
            worker = self.extraction if role == "extraction" else self.solidworks
            result = getattr(worker, task)(*args)
            self.harness.validate_handoff(task, result)
            summary.update(observation(result))
            event.update(summary)
            return result

    def watch_node(self, role, task, node):
        """Persist worker outcomes without renaming any existing graph nodes.

        Only non-interrupting work nodes are wrapped; human pauses remain pure.
        Older checkpoints simply start without the additive worker_status field.
        """
        def watched(state):
            # One ledger per checkpointed job/node, even when a compiled graph
            # is reused for several jobs or invoked from different threads.
            ledger = AgentHarness(self._harness.policy, clock=self._harness.clock)
            try:
                if state.get("harness") is not None:
                    ledger.restore(state["harness"])
            except HarnessStopped as ex:
                return {"status": "needs_human", "detail": str(ex),
                        "events": [*state.get("events", []), f"harness stopped: {ex}"]}
            with ledger.session():
                with self._task(role, task) as event:
                    try:
                        result = node(state)
                    except HarnessStopped as ex:
                        result = {"status": "needs_human", "detail": str(ex),
                                  "events": [*state.get("events", []), f"harness stopped: {ex}"]}
                    statuses = dict(state.get("worker_status", {}))
                    statuses[role] = {"task": task, "status": result.get("status", "completed")}
                    summary = observation(result)
                    event.update(summary)
                    self.harness.record("evaluation", role, task, **summary)
                    proposal = result.get("proposal")
                    if isinstance(proposal, dict):
                        self.harness.record("decision", role, "propose_repair", item_id=str(proposal["item_id"])[:80],
                                            summary=str(proposal.get("reason", ""))[:300], next="human_approval")
                    return {**result, "worker_status": statuses, "harness": self.harness.snapshot()}
        return watched

    def read_page(self, page_no, png, boxes):
        return self._dispatch("extraction", "read_page", page_no, png, boxes)

    def assess_page(self, png):
        return self._dispatch("extraction", "assess_page", png)

    def read_bom(self, result, pictures):
        return self._dispatch("extraction", "read_bom", result, pictures)

    def read_list(self, parts):
        return self._dispatch("extraction", "read_list", parts)

    def read_hybrid(self, page_no, png, ocr, strategy, transport="verbose"):
        return self._dispatch("extraction", "read_hybrid", page_no, png, ocr, strategy, transport)

    def interpret(self, model, pictures):
        return self._dispatch("extraction", "interpret", model, pictures)

    def plan(self, model, pictures):
        if model.issues:
            # Preserve the original zero-call response for unresolved evidence.
            from app.drawing.planner import plan
            return plan(None, model, pictures)
        observed = self._dispatch("extraction", "interpret", model, pictures)
        return self._dispatch("solidworks", "plan", model, observed)

    def repair(self, parts):
        return self._dispatch("solidworks", "repair", parts)

    def build_part(self, approved, job_dir):
        return self._dispatch("solidworks", "build_part", approved, job_dir)

    async def build_assembly(self, sw, plan, assets, output, approved_hash, *, review_only=False):
        with self.harness.session(), self.harness.action("solidworks", "build_assembly") as summary, self._task("solidworks", "build_assembly") as event:
            result = await self.solidworks.build_assembly(sw, plan, assets, output, approved_hash, review_only=review_only)
            self.harness.validate_handoff("build_assembly", result)
            summary.update(observation(result))
            event.update(summary)
            return result

    async def build_sheet(self, sw, plan, output, approved_hash):
        with self.harness.session(), self.harness.action("solidworks", "build_sheet") as summary, self._task("solidworks", "build_sheet") as event:
            result = await self.solidworks.build_sheet(sw, plan, output, approved_hash)
            self.harness.validate_handoff("build_sheet", result)
            summary.update(observation(result))
            event.update(summary)
            return result
