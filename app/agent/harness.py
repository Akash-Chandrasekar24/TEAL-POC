"""Bounded worker actions, model budgets and public ReAct loop observations.

This is the runtime envelope around the existing algorithms, not another LLM
or retry engine. Decisions contain short task summaries, never private reasoning.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import json
import math
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from app import trace
from app.llm.base import LLMError


ACTIONS = {
    "extraction": frozenset({"read_page", "read_hybrid", "interpret", "read_bom", "read_list", "assess_page", "suggest_clarifications"}),
    "solidworks": frozenset({"plan", "repair", "build_part", "build_assembly", "build_sheet"}),
}


class HarnessStopped(LLMError):
    """A runtime boundary requires human attention; never automatically retried."""


@dataclass(frozen=True)
class HarnessPolicy:
    max_model_calls: int = 64
    max_tokens: int = 1_000_000
    max_model_seconds: float = 7200.0
    max_actions: int = 128
    max_identical_repairs: int = 2
    history_limit: int = 64

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"Harness {name} must be positive and finite")
            if name != "max_model_seconds" and not isinstance(value, int):
                raise ValueError(f"Harness {name} must be an integer")

    @classmethod
    def configured(cls):
        return cls(max_model_calls=int(os.getenv("HARNESS_MAX_MODEL_CALLS", "64")),
                   max_tokens=int(os.getenv("HARNESS_MAX_TOKENS", "1000000")),
                   max_model_seconds=float(os.getenv("HARNESS_MAX_MODEL_SECONDS", "7200")),
                   max_actions=int(os.getenv("HARNESS_MAX_ACTIONS", "128")))


_CURRENT = contextvars.ContextVar("agent_harness", default=None)


def current_harness():
    return _CURRENT.get()


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise HarnessStopped("Invalid model usage counters; inspect the provider report")
    return value


def observation(result) -> dict:
    """A small allowlisted summary; never copy a drawing, prompt or raw reply."""
    if isinstance(result, dict):
        status = result.get("status")
        if not isinstance(status, str):
            report = result.get("report") or {}
            if report.get("ok") is False:
                status = "failed"
            elif report.get("ok") is True:
                status = "awaiting_review" if (result.get("finish") or {}).get("ok") is True else "needs_human"
            else:
                status = "returned"
        out = {"status": status[:80]}
        report = result.get("report") or {}
        failure = result.get("failure") or report.get("failure") or {}
        if failure:
            out["failure"] = {k: str(failure[k])[:240] for k in ("item_id", "kind", "code", "message") if k in failure}
        questions = result.get("questions") or result.get("reasons")
        if isinstance(questions, list):
            out["unresolved"] = len(questions)
        return out
    if hasattr(result, "dims") and hasattr(result, "views"):
        return {"status": "extracted", "dimensions": len(result.dims), "views": len(result.views)}
    return {"status": "returned"}


class AgentHarness:
    def __init__(self, policy: HarnessPolicy | None = None, *, clock=time.monotonic):
        self.policy = policy or HarnessPolicy.configured()
        self.clock, self._lock = clock, threading.RLock()
        self.state = {"version": 1, "actions": 0, "model_calls": 0, "cached_calls": 0, "tokens": 0,
                      "model_seconds": 0.0, "history": [], "repair_counts": {}}

    @contextmanager
    def session(self, saved: dict | None = None):
        if _CURRENT.get() is self:
            yield self
            return
        if saved is not None:
            self.restore(saved)
        token = _CURRENT.set(self)
        try:
            yield self
        finally:
            _CURRENT.reset(token)

    def restore(self, saved):
        if not isinstance(saved, dict) or saved.get("version") != 1:
            raise HarnessStopped("Unrecognized saved harness state; inspect the job before resuming")
        try:
            policy = HarnessPolicy(**saved["policy"])
            for key in ("actions", "model_calls", "cached_calls", "tokens", "model_seconds"):
                _number(saved.get(key))
        except (KeyError, TypeError, ValueError):
            raise HarnessStopped("Invalid saved harness budget; inspect the job") from None
        if not isinstance(saved.get("history"), list) or not isinstance(saved.get("repair_counts"), dict):
            raise HarnessStopped("Invalid saved harness history")
        if any(not isinstance(h, dict) for h in saved["history"]) or any(
                not isinstance(k, str) or type(v) is not int or v < 0 for k, v in saved["repair_counts"].items()):
            raise HarnessStopped("Invalid saved harness observations or repair counters")
        with self._lock:
            self.policy = policy
            self.state = copy.deepcopy({k: saved[k] for k in self.state})
            self.state["history"] = self.state["history"][-policy.history_limit:]

    def snapshot(self):
        with self._lock:
            return {**copy.deepcopy(self.state), "policy": asdict(self.policy)}

    def record(self, phase, role, task, **details):
        item = {"phase": phase, "worker": role, "task": task, **details}
        with self._lock:
            self.state["history"] = [*self.state["history"], item][-self.policy.history_limit:]
        trace.current().event("harness", phase, **{k: v for k, v in item.items() if k != "phase"})

    def begin_action(self, role, task):
        if role not in ACTIONS or task not in ACTIONS[role]:
            raise HarnessStopped(f"Action {role}.{task} is not assigned to that worker")
        with self._lock:
            if self.state["actions"] >= self.policy.max_actions:
                raise HarnessStopped("Agent action budget reached; review the job")
            self.state["actions"] += 1
        self.record("decision", role, task, summary="Dispatch the assigned operation under the existing validation and approval gates")
        self.record("action", role, task)

    def validate_handoff(self, task, result):
        if task == "plan":
            if (not isinstance(result, dict) or result.get("status") not in
                    {"needs_review", "needs_information", "ready_for_approval"} or result.get("approved") is not False):
                raise HarnessStopped("Planning returned an invalid or preapproved handoff")
            if result["status"] == "ready_for_approval" and (not isinstance(result.get("draft"), dict) or result.get("questions")):
                raise HarnessStopped("A ready plan must contain a draft and no unresolved questions")
            if result["status"] == "ready_for_approval":
                from app.menu import parse_reply
                from app.menu.buildlist import Stopped
                from app.runner import validate_list
                try:
                    parsed = parse_reply(result["draft"])
                    if isinstance(parsed, Stopped) or validate_list(parsed):
                        raise ValueError("Draft is not approvable")
                except ValueError:
                    raise HarnessStopped("The ready draft failed the existing CAD validation gate") from None
        elif task == "build_part":
            if not isinstance(result, dict) or not isinstance(result.get("report"), dict) or type(result["report"].get("ok")) is not bool:
                raise HarnessStopped("CAD execution returned an incomplete report; inspect the model")
            if result["report"]["ok"] is False and not isinstance(result["report"].get("failure"), dict):
                raise HarnessStopped("A failed CAD build must identify its failure")
            report = result["report"]
            if not isinstance(report.get("items"), list):
                raise HarnessStopped("CAD execution returned no item reports; inspect the model")
            if report["ok"] is False:
                failure = report["failure"]
                if (failure.get("kind") not in {"request", "check", "tool", "environment", "internal"}
                        or any(not isinstance(failure.get(k), str) or not failure[k] for k in ("item_id", "feature", "message"))):
                    raise HarnessStopped("CAD execution returned an incomplete failure; inspect the model")
            finish = result.get("finish")
            if finish is not None and (not isinstance(finish, dict) or type(finish.get("ok")) is not bool):
                raise HarnessStopped("CAD execution returned an invalid export report")
            if finish and finish["ok"] and (not isinstance(finish.get("files"), dict) or not finish["files"]):
                raise HarnessStopped("CAD execution reported completion without export files")
        elif task in {"build_assembly", "build_sheet"}:
            if not isinstance(result, dict) or result.get("status") not in {"failed", "awaiting_review"}:
                raise HarnessStopped("Assembly execution returned an invalid result; inspect the model")
            if result["status"] == "awaiting_review" and (
                    not result.get("files") or result.get("errors") or not isinstance(result.get("checks"), list)
                    or not result["checks"] or any(c.get("ok") is not True for c in result["checks"])):
                raise HarnessStopped("Assembly completion lacks verified outputs; inspect the model")

    @contextmanager
    def action(self, role, task):
        self.begin_action(role, task)
        summary = {}
        try:
            yield summary
        except Exception as ex:
            self.record("observation", role, task, status="failed", error_type=type(ex).__name__)
            self.record("evaluation", role, task, status="failed", next="existing_error_handler")
            raise
        self.record("observation", role, task, **summary)
        self.record("evaluation", role, task, status=summary.get("status", "returned"),
                    next="existing_validation_or_human_gate")

    def model_call(self, inner, system, parts):
        with self._lock:
            if self.state["model_calls"] >= self.policy.max_model_calls:
                raise HarnessStopped("Model call budget reached; review the job")
            if self.state["tokens"] >= self.policy.max_tokens:
                raise HarnessStopped("Model token budget reached; review the job")
            if self.state["model_seconds"] >= self.policy.max_model_seconds:
                raise HarnessStopped("Active model time budget reached; review the job")
            self.state["model_calls"] += 1  # failures and cache lookups count as attempts
        started = self.clock()
        try:
            response = inner.generate_json(system, parts)
            usage = response.usage
            if not isinstance(usage, dict):
                raise HarnessStopped("Invalid model usage report; inspect the provider response")
            values = [_number(usage.get(k, 0)) for k in ("prompt_tokens", "output_tokens", "thought_tokens")]
            with self._lock:
                self.state["tokens"] += sum(values)
                self.state["cached_calls"] += bool(usage.get("cached"))
            return response
        except HarnessStopped:
            getattr(inner, "discard", lambda *a: None)(system, parts)
            raise
        finally:
            with self._lock:
                self.state["model_seconds"] += max(0.0, self.clock() - started)

    def repair_seen(self, approved_hash, item_id, params):
        digest = hashlib.sha256(json.dumps([approved_hash, item_id, params], sort_keys=True,
                                          separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        with self._lock:
            count = self.state["repair_counts"].get(digest, 0) + 1
            self.state["repair_counts"][digest] = count
        return count > self.policy.max_identical_repairs


class HarnessClient:
    """Meter any existing/injected client without adding prompts or retries."""
    def __init__(self, inner):
        self.inner, self.model = inner, inner.model
        self.provider = getattr(inner, "provider", None)
        self.cache_settings = getattr(inner, "cache_settings", {})

    def generate_json(self, system, parts):
        harness = _CURRENT.get()
        return harness.model_call(self.inner, system, parts) if harness else self.inner.generate_json(system, parts)

    def discard(self, system, parts):
        getattr(self.inner, "discard", lambda *a: None)(system, parts)


def capabilities():
    from app import config
    return {"mode": "bounded_react", "workers": {k: sorted(v) for k, v in ACTIONS.items()},
            "policy": asdict(HarnessPolicy.configured()), "approval_required": True,
            "model": {"provider": config.WORKER_LLM_PROVIDER, "domo_model": config.DOMO_MODEL,
                      "reasoning_effort": config.DOMO_REASONING_EFFORT,
                      "extraction_reasoning_effort": config.EXTRACTION_REASONING_EFFORT,
                      "cad_reasoning_effort": config.CAD_REASONING_EFFORT},
            "provider_retries_owned_by": "existing_job_graph", "native_execution": "serialized"}
