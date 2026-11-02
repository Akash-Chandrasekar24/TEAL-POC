"""Step verifier: every model-changing step is measured before and after, and judged against an expectation.

Lessons from building real parts that this class enforces:
- A step can report COMPLETED and change nothing (a boss with `reversed` on an offset plane): the
  volume change is compared with the expected one, and "no change" is its own verdict.
- The active document can silently become another one : the document name is checked on both sides.
- analyze_model sometimes returns "Error executing tool" for no reason : one retry, then a verdict.
- A call that times out may still have executed : after a timeout the state is read BEFORE any retry.
Volume cannot see mirrors or shifted features: features add their own position probes on top of this.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable

from app.mcp_client import McpToolTimeout, ToolResult
from .run_log import RunLog

MM3 = 1e-9                  # m3 per mm3


def volume_quantum_mm3(*volumes_m3: float) -> float:
    """Smallest step in which analyze_model reports a volume, in mm3. SolidWorks gives 6 significant digits
    (0.000433944 m3 -> 1 mm3; 1.09653e-3 m3 -> 10 mm3), so a difference of two volumes is only known to about one quantum.
    Measured 2026-10-12: a correct 20.94 mm3 chamfer showed as -21.00 mm3 against an allowed +-0.5 mm3."""
    top = max((abs(v) for v in volumes_m3), default=0.0) / MM3
    return 10.0 ** (math.floor(math.log10(top)) - 5) if top > 0 else 0.0
NOOP_EPS_M3 = 1e-10         # 0.1 mm3: smaller changes count as "nothing happened"


class VerifyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Snapshot:
    doc: str
    volume: float           # m3
    area: float             # m2
    cog: tuple[float, float, float]
    bodies: int
    faces: int
    edges: int
    vertices: int

    def same_shape(self, other: "Snapshot") -> bool:
        return (self.faces, self.edges, self.vertices, self.bodies) == (other.faces, other.edges, other.vertices, other.bodies) \
            and abs(self.volume - other.volume) < NOOP_EPS_M3


@dataclass
class Expect:
    """What a step should do to the volume (all in mm3, signed: cuts are negative)."""
    delta_mm3: float | None = None            # exact expectation (computed in code from the request)
    rel_tol: float = 0.001                    # 0.1 %
    abs_tol_mm3: float = 0.5
    bound_mm3: float | None = None            # for uncertain cuts: |measured| must be <= this (and > 0)
    must_change: bool = True                  # False only for steps that legitimately change nothing
    regression_delta_mm3: float | None = None # a previously measured value for the same step (optional regression check)
    regression_rel_tol: float = 0.02
    range_mm3: tuple[float, float] | None = None   # (lowest, highest) signed change when the exact value is not known (both sides of an arc):
                                                   # the measured change may lie anywhere inside, padded by abs_tol and the volume resolution
    expect_doc: str | None = None
    bodies: int | None = None                 # expected body count after the step (a boss that only touches could create a 2nd body)


@dataclass
class StepVerdict:
    name: str
    ok: bool
    verdict: str              # ok | tool_failed | no_op | wrong_delta | above_bound | wrong_sign | doc_changed | analyze_failed | timeout
    message: str
    before: Snapshot | None = None
    after: Snapshot | None = None
    measured_delta_mm3: float = 0.0
    expected_delta_mm3: float | None = None
    bound_mm3: float | None = None
    regression: dict[str, Any] | None = None
    result: ToolResult | None = None
    warnings: list[str] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "verdict": self.verdict, "message": self.message,
                "expected_delta_mm3": self.expected_delta_mm3, "measured_delta_mm3": self.measured_delta_mm3,
                "bound_mm3": self.bound_mm3, "regression": self.regression,
                "before": self.before.__dict__ if self.before else None, "after": self.after.__dict__ if self.after else None,
                "tool_text": (self.result.text[:300] if self.result else None)}


async def snapshot(sw, retries: int = 1) -> Snapshot:
    """mass_properties + geometry of the active part. One retry on the intermittent analyze_model error."""
    last = ""
    for _ in range(retries + 1):
        ge = await sw.analyze_model("geometry")
        if not ge.ok:
            last = ge.text[:200]
            continue
        g = ge.numbers()
        m = re.search(r"document=([^ |]+)", ge.text)
        doc = m.group(1) if m else "?"
        counts = dict(bodies=int(g.get("bodies", 0)), faces=int(g.get("faces", 0)),
                      edges=int(g.get("edges", 0)), vertices=int(g.get("vertices", 0)))
        if counts["bodies"] == 0:
            # An EMPTY part: mass_properties raises "Error executing tool" there (measured), so volume is 0 by definition.
            return Snapshot(doc=doc, volume=0.0, area=0.0, cog=(0.0, 0.0, 0.0), **counts)
        mp = await sw.analyze_model("mass_properties")
        if mp.ok:
            n = mp.numbers()
            return Snapshot(doc=doc, volume=n["volume"], area=n.get("surface_area", 0.0),
                            cog=(n.get("cx", 0.0), n.get("cy", 0.0), n.get("cz", 0.0)), **counts)
        last = mp.text[:200]
    raise VerifyError(f"analyze_model failed twice: {last}")


def judge(name: str, before: Snapshot, after: Snapshot, exp: Expect, result: ToolResult | None = None) -> StepVerdict:
    """Pure function: turns two snapshots and an expectation into a verdict (unit-tested offline)."""
    delta = (after.volume - before.volume) / MM3
    v = StepVerdict(name=name, ok=False, verdict="", message="", before=before, after=after, measured_delta_mm3=delta,
                    expected_delta_mm3=exp.delta_mm3, bound_mm3=exp.bound_mm3, result=result)
    if exp.regression_delta_mm3 is not None:
        reg = exp.regression_delta_mm3
        reg_ok = abs(delta - reg) <= max(abs(reg) * exp.regression_rel_tol, exp.abs_tol_mm3)
        v.regression = {"reference_mm3": reg, "measured_mm3": round(delta, 3), "ok": reg_ok}
        if not reg_ok:
            v.warnings.append(f"regression drift: reference measured {reg:.1f} mm3, now {delta:.1f} mm3")
    if exp.expect_doc and after.doc != exp.expect_doc:
        v.verdict, v.message = "doc_changed", f"active document is {after.doc!r}, expected {exp.expect_doc!r}"
        return v
    if before.doc != after.doc:
        v.verdict, v.message = "doc_changed", f"active document changed from {before.doc!r} to {after.doc!r}"
        return v
    if exp.bodies is not None and after.bodies != exp.bodies:
        v.verdict, v.message = "wrong_bodies", f"{after.bodies} bodies after the step, expected {exp.bodies}"
        return v
    if exp.must_change and before.same_shape(after):
        v.verdict = "no_op"
        v.message = "the tool reported success but the model did not change (same volume and same face/edge/vertex counts)"
        return v
    if exp.range_mm3 is not None:
        lo, hi = sorted(exp.range_mm3)
        pad = max(exp.abs_tol_mm3, volume_quantum_mm3(before.volume, after.volume) * 1.001)
        if not (lo - pad <= delta <= hi + pad):
            v.verdict = "wrong_delta"
            v.message = f"volume change {delta:.2f} mm3, expected between {lo:.2f} and {hi:.2f} mm3 (allowed +-{pad:.2f} outside that range)"
            return v
    if exp.delta_mm3 is not None:
        tol = max(abs(exp.delta_mm3) * exp.rel_tol, exp.abs_tol_mm3, volume_quantum_mm3(before.volume, after.volume) * 1.001)
        if abs(delta - exp.delta_mm3) > tol:
            v.verdict = "wrong_delta"
            v.message = f"volume change {delta:.2f} mm3, expected {exp.delta_mm3:.2f} mm3 (allowed +-{tol:.2f})"
            return v
    if exp.bound_mm3 is not None:
        if (exp.bound_mm3 < 0) != (delta < 0) and abs(delta) > exp.abs_tol_mm3:
            v.verdict, v.message = "wrong_sign", f"volume changed by {delta:.2f} mm3 but the bound is {exp.bound_mm3:.2f} (opposite sign)"
            return v
        if abs(delta) > abs(exp.bound_mm3) + max(exp.abs_tol_mm3, volume_quantum_mm3(before.volume, after.volume) * 1.001):
            v.verdict, v.message = "above_bound", f"|change| {abs(delta):.2f} mm3 exceeds the upper bound {abs(exp.bound_mm3):.2f} mm3"
            return v
    v.ok, v.verdict = True, "ok"
    v.message = f"volume change {delta:.2f} mm3" + (f" (expected {exp.delta_mm3:.2f})" if exp.delta_mm3 is not None else
                                                     f" (bound {exp.bound_mm3:.2f})" if exp.bound_mm3 is not None else "")
    return v


class StepVerifier:
    """`doc`: the document this verifier works on. Before each step the active document is checked; if it is another one
    (seen in practice: the execution layer reported another open document between two calls),
    it is re-activated and a warning is recorded on the verdict. Set it right after opening the part: verifier.doc = name."""

    def __init__(self, sw, log: RunLog | None = None, doc: str | None = None):
        self.sw, self.log, self.doc = sw, log, doc
        self.steps: list[StepVerdict] = []

    async def _on_right_document(self) -> tuple[Snapshot, list[str]]:
        before = await snapshot(self.sw)
        warnings: list[str] = []
        if self.doc and before.doc != self.doc:
            r = await self.sw.activate_document(self.doc)
            warnings.append(f"active document was {before.doc!r}; re-activated {self.doc!r} ({'ok' if r.ok else 'FAILED: ' + r.text[:100]})")
            before = await snapshot(self.sw)
        return before, warnings

    async def run_step(self, name: str, action: Callable[[], Awaitable[ToolResult]], exp: Expect,
                       retry_on_timeout: bool = True) -> StepVerdict:
        """Measure, run `action` (a coroutine returning a ToolResult), measure again, judge."""
        try:
            before, pre_warnings = await self._on_right_document()
        except VerifyError as ex:
            return self._done(StepVerdict(name, False, "analyze_failed", str(ex)))
        if self.doc and exp.expect_doc is None:
            exp = replace(exp, expect_doc=self.doc)
        result: ToolResult | None = None
        try:
            result = await action()
        except McpToolTimeout as ex:
            # The call may have executed. Look at the model BEFORE deciding to retry (S1-09).
            mid = await snapshot(self.sw)
            if retry_on_timeout and mid.same_shape(before):
                result = await action()
            elif not mid.same_shape(before):
                result = ToolResult("(timeout)", f"timed out but the model changed: {ex}", False, 0.0)
            else:
                return self._done(StepVerdict(name, False, "timeout", f"{ex}; model unchanged, not retried", before=before))
        if not result.ok:
            return self._done(StepVerdict(name, False, "tool_failed", result.text[:400], before=before, result=result))
        try:
            after = await snapshot(self.sw)
        except VerifyError as ex:
            return self._done(StepVerdict(name, False, "analyze_failed", str(ex), before=before, result=result))
        v = judge(name, before, after, exp, result)
        v.warnings = pre_warnings + v.warnings
        return self._done(v)

    async def current(self) -> Snapshot:
        """Snapshot of OUR document (re-activating it if the active one drifted)."""
        snap, _ = await self._on_right_document()
        return snap

    def _done(self, v: StepVerdict) -> StepVerdict:
        self.steps.append(v)
        if self.log:
            self.log.step(v.record())
        return v
