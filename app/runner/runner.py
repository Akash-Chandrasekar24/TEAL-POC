"""The RUNNER: builds the part from an approved build list. Plain Python, no LLM, never decides anything the list does not say.

    prepare(list)  OFFLINE: every item -> its feature request (menu adapter), every request validated. A bad number is found here,
                   before SolidWorks is touched.
    Runner.run     ensure SolidWorks is ready, close leftover scratch parts, open a new part with the unit's template, then for each item:
                   feature.apply (the feature verifies its own step) -> an ItemReport. The first failure stops the run.

A failure is classified for whoever handles it next (the orchestrator's repair step or the human):
    request      the numbers cannot be built (refused before anything is sent)       -> a corrected item is needed
    check        SolidWorks answered but volume / faces / positions are not as expected -> a corrected item or a human look
    tool         SolidWorks refused the operation (a graph / content error)             -> a corrected item
    environment  SolidWorks / the connection is not in a usable state                   -> the runner retries ONCE with a fresh part
    internal     an unexpected exception (a bug): never retried
After a failed run the part is left open (the report names it) and the next run closes it as a leftover scratch part.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app import config
from app.features.anchors import anchors_of
from app.features.extents import predicted_check
from app.features.base import FeatureContext, FeatureRequestError, read_geometry
from app.menu import MENU, BuildContext, BuildList, base_info, list_gate
from app.menu.buildlist import BASE_FEATURES, Item
from app.mcp_client import McpToolTimeout, SolidWorksClient
from .errors import classify_failure
from app.verify import RunLog, StepVerifier, VerifyError, ensure_clean_workspace
from app.verify.bbox import exact_bbox


class RunnerRefused(Exception):
    """The list is not allowed to run (the gate found reasons)."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class RequestProblem(Exception):
    """An item's numbers cannot be built; found offline."""

    def __init__(self, item_id: str, feature: str, message: str):
        super().__init__(f"{item_id} ({feature}): {message}")
        self.item_id, self.feature, self.message = item_id, feature, message


@dataclass
class Prepared:
    item: Item
    params: BaseModel
    feature: Any
    label: str


@dataclass
class ItemReport:
    id: str
    feature: str
    status: str                                  # ok | failed | not_run
    seconds: float = 0.0
    message: str = ""
    verdicts: list[dict] = field(default_factory=list)
    probes: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Failure:
    item_id: str
    feature: str
    kind: str                                    # request | check | tool | environment | internal
    message: str
    code: str = ""                               # the SolidWorks error code, when there is one
    detail: list[str] = field(default_factory=list)


@dataclass
class RunReport:
    part_name: str
    unit: str
    ok: bool = False
    items: list[ItemReport] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)          # accepted unsupported items: not built
    failure: Failure | None = None
    document: str | None = None
    final: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    attempts: int = 1
    log_dir: str = ""

    def lines(self) -> list[str]:
        out = [f"{'OK' if self.ok else 'FAILED'}: {self.part_name or 'part'} ({self.unit}), {sum(i.status == 'ok' for i in self.items)}/{len(self.items)} items"]
        for it in self.items:
            out.append(f"  {it.id} {it.feature}: {it.status} ({it.seconds:.1f} s)" + (f" | {it.message}" if it.message else ""))
        out += [f"  gap (not built): {g['id']} {g['text']}" for g in self.gaps]
        if self.failure:
            f = self.failure
            out.append(f"  FAILURE at {f.item_id} ({f.feature}), kind {f.kind}{' ' + f.code if f.code else ''}: {f.message}")
        if self.final:
            out.append(f"  final: {self.final}")
        out += [f"  warning: {w}" for w in self.warnings]
        return out


def template_for_unit(unit: str) -> tuple[str | None, str | None]:
    """(template path or None, warning): mm -> SW_TEMPLATE_MM, inch -> the explicit inch template (see README.md, part templates)."""
    if unit == "inch":
        return config.SW_TEMPLATE_INCH, None
    if config.SW_TEMPLATE_MM:
        if not Path(config.SW_TEMPLATE_MM).is_file():
            return None, f"SW_TEMPLATE_MM does not exist: {config.SW_TEMPLATE_MM} (using the default template)"
        return config.SW_TEMPLATE_MM, None
    return None, "no millimetre template configured (SW_TEMPLATE_MM): the default template is used, so STEP / SLDPRT units are inches"


# ------------------------------------------------------------------ offline preparation
def prepare(bl: BuildList) -> list[Prepared]:
    """Every item -> its validated feature request, in order. Raises RequestProblem on the first item that cannot be built."""
    ctx = BuildContext(bl.unit)
    out: list[Prepared] = []
    for it in bl.items:
        entry = MENU.get(it.feature)
        if entry is None:
            raise RequestProblem(it.id, it.feature, f"'{it.feature}' is not on the menu")
        label = f"{it.id} {it.feature}"
        try:
            params = entry.params_model.model_validate(it.params)
            feature = entry.build(params, ctx, label)
            feature.validate()
        except ValidationError as ex:
            first = ex.errors()[0]
            raise RequestProblem(it.id, it.feature, f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}") from ex
        except FeatureRequestError as ex:
            raise RequestProblem(it.id, it.feature, str(ex)) from ex
        if it.feature in BASE_FEATURES and not ctx.has_base:
            (w, h), shift, thickness = base_info(params, bl.unit)
            ctx.outline_wh, ctx.frame_shift, ctx.thickness = (w, h), shift, thickness
            ctx.turned = it.feature == "revolve_base"
            ctx.base_feature = feature
            if ctx.turned:
                ctx.turned_profile, ctx.turned_axis = feature.profile, feature.axis
        ctx.anchors.update(anchors_of(it.id, feature))                         # the flat faces this item made: later items can say "on <id>.top"
        out.append(Prepared(it, params, feature, label))
    return out


def validate_list(bl: BuildList) -> list[str]:
    """Everything that can be known WITHOUT SolidWorks: the gate (format, needs_human, rule 12, ...) and whether every item can be built.
    Empty = the list is fine to show to the human and to build. Run this before asking for approval, so nobody approves an unbuildable list."""
    reasons = list_gate(bl)
    if not reasons:
        try:
            prepare(bl)
        except RequestProblem as ex:
            reasons.append(f"cannot be built: {ex}")
    return reasons


# ------------------------------------------------------------------ the runner
class Runner:
    def __init__(self, sw: SolidWorksClient, *, expected_docs: tuple[str, ...] = (), log: RunLog | None = None, env_retries: int = 1):
        self.sw, self.expected_docs, self.log, self.env_retries = sw, tuple(expected_docs), log, env_retries

    async def run(self, bl: BuildList, *, gate: bool = True) -> RunReport:
        """Build the whole list. Never raises for a failed step (the report says what failed); raises RunnerRefused / RequestProblem before any work."""
        if gate:
            reasons = validate_list(bl)
            if reasons:
                raise RunnerRefused(reasons)
        prepared = prepare(bl)
        attempts = 0
        while True:
            attempts += 1
            report = await self._run_once(bl, prepared)
            report.attempts = attempts
            if report.ok or report.failure is None or report.failure.kind != "environment" or attempts > self.env_retries:
                return report
            self._event("environment_retry", failure=report.failure.message)

    # ---------------------------------------------------------------- internals
    def _event(self, kind: str, **data: Any) -> None:
        if self.log:
            self.log.event(kind, **data)

    async def _open_part(self, unit: str, report: RunReport) -> str:
        template, warning = template_for_unit(unit)
        if warning:
            report.warnings.append(warning)
        await self.sw.ensure_ready()
        await ensure_clean_workspace(self.sw, expected=self.expected_docs)
        args = {"template_path": template} if template else {}
        r = await self.sw.call("open_new_part", args)
        if not r.ok:
            raise RuntimeError(f"open_new_part failed: {r.text[:300]}")
        import re
        m = re.search(r"document=([^ |]+)", r.text)
        if not m:
            raise RuntimeError(f"open_new_part gave no document name: {r.text[:200]}")
        return m.group(1)

    async def _run_once(self, bl: BuildList, prepared: list[Prepared]) -> RunReport:
        report = RunReport(bl.part_name, bl.unit, log_dir=str(self.log.dir) if self.log else "")
        report.items = [ItemReport(p.item.id, p.item.feature, "not_run") for p in prepared]
        report.gaps = [{"id": u.id, "text": u.text} for u in bl.unsupported if u.id in set(bl.accepted_gaps)]
        try:
            report.document = await self._open_part(bl.unit, report)
        except (McpToolTimeout, VerifyError, RuntimeError, ConnectionError, OSError) as ex:
            report.failure = Failure("-", "-", "environment", f"could not open a part: {ex}")
            return report
        verifier = StepVerifier(self.sw, self.log, doc=report.document)
        ctx = FeatureContext(self.sw, verifier, self.log)
        for p, rep in zip(prepared, report.items):
            t0 = time.time()
            self._event("item_start", id=p.item.id, feature=p.item.feature)
            try:
                result = await p.feature.apply(ctx)
            except FeatureRequestError as ex:
                self._finish_item(rep, t0, "failed", str(ex))
                report.failure = Failure(p.item.id, p.item.feature, "request", str(ex))
                return report
            except (McpToolTimeout, VerifyError, ConnectionError) as ex:
                self._finish_item(rep, t0, "failed", str(ex))
                report.failure = Failure(p.item.id, p.item.feature, "environment", f"{type(ex).__name__}: {ex}")
                return report
            except Exception as ex:                                                  # a bug: never hidden, never retried
                self._finish_item(rep, t0, "failed", f"{type(ex).__name__}: {ex}")
                report.failure = Failure(p.item.id, p.item.feature, "internal", f"{type(ex).__name__}: {ex}", detail=traceback.format_exc().splitlines()[-6:])
                return report
            rep.verdicts = [v.record() for v in result.verdicts]
            for v in rep.verdicts:
                v.pop("before", None)
                v.pop("after", None)
            rep.probes = [{"name": q.name, "ok": q.ok, "detail": q.detail} for q in result.probes]
            rep.notes = list(result.notes)
            if result.ok:
                self._finish_item(rep, t0, "ok", "")
                continue
            report.failure = self._classify(p, result)
            self._finish_item(rep, t0, "failed", report.failure.message)
            return report
        report.ok = True
        report.final = await self._final(verifier, report)
        mode = config.PREDICTED_BBOX_MODE if config.PREDICTED_BBOX_MODE in ("warn", "stop") else "warn"
        pc = predicted_check(report.final, [p.feature for p in prepared], bl.unit, config.PREDICTED_BBOX_TOL_MM, mode)
        if pc is not None:                                                          # where the items' material can be vs where the part is
            report.final["predicted_check"] = pc
            if not pc["ok"]:
                report.warnings.append(f"PREDICTED BOUNDING BOX VIOLATED ({mode} mode): {pc['detail']}")
        if bl.expected_final is not None:                                           # the human's own numbers for the finished part
            checks = bl.expected_final.check(report.final)
            report.final["expected_checks"] = checks
            report.warnings += [f"EXPECTED FINAL CHECK FAILED: {c['name']}: {c['detail']}" for c in checks if not c["ok"]]
        return report

    def _finish_item(self, rep: ItemReport, t0: float, status: str, message: str) -> None:
        rep.status, rep.message, rep.seconds = status, message, time.time() - t0
        self._event("item_end", id=rep.id, status=status, message=message, seconds=round(rep.seconds, 2))

    @staticmethod
    def _classify(p: Prepared, result) -> Failure:
        bad = next((v for v in result.verdicts if not v.ok), None)
        if bad is not None:
            if bad.verdict == "tool_failed":
                info = classify_failure(bad.message)
                return Failure(p.item.id, p.item.feature, "environment" if info.is_environment else "tool", bad.message[:400], code=info.code)
            if bad.verdict in ("timeout", "analyze_failed"):
                return Failure(p.item.id, p.item.feature, "environment", f"{bad.verdict}: {bad.message}"[:400])
            return Failure(p.item.id, p.item.feature, "check", f"{bad.name}: {bad.verdict}: {bad.message}"[:400], code=bad.verdict)
        failed = [q for q in result.probes if not q.ok]
        return Failure(p.item.id, p.item.feature, "check", f"probe '{failed[0].name}' failed: {failed[0].detail}"[:400], code="probe",
                       detail=[f"{q.name}: {q.detail}" for q in failed])

    async def _final(self, verifier: StepVerifier, report: RunReport) -> dict:
        snap = await verifier.current()
        final = {"volume_mm3": round(snap.volume * 1e9, 2), "faces": snap.faces, "edges": snap.edges, "bodies": snap.bodies, "document": snap.doc}
        try:
            from app.features.base import fast_bbox
            bb = await fast_bbox(self.sw)
            if bb is None:
                faces, edges = await read_geometry(self.sw)
                bb = exact_bbox(faces, edges)
            final["bbox_mm"] = [[round(v * 1000, 3) for v in bb.lo], [round(v * 1000, 3) for v in bb.hi]]
            if bb.uncertain:
                report.warnings.append(f"bounding box uncertain for: {bb.uncertain}")
        except Exception as ex:                                                      # the bbox is information, not a check
            report.warnings.append(f"bounding box not available: {type(ex).__name__}: {ex}")
        return final
