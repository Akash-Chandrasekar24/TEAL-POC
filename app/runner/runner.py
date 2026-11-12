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

import asyncio
import json
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
    measured: dict = field(default_factory=dict)          # drawing vs model for this step (app.runner.measure): where it is, its numbers, the deviation


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
    agent: dict = field(default_factory=dict)               # the SolidWorks agent's calls (when one took part)

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


def _say(item) -> str:
    """A step in a few words for the page: the feature and the printed text it comes from."""
    return f"{item.feature.replace('_', ' ')}" + (f" ({item.printed})" if getattr(item, "printed", "") else "")


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
        ctx.height_varies |= it.feature == "side_profile_cut"
        ctx.anchors.update(anchors_of(it.id, feature))                         # the flat faces this item made: later items can say "on <id>.top"
        out.append(Prepared(it, params, feature, label))
    if len(out) > 1 and out[1].item.feature == "side_profile_cut" and getattr(out[0].feature, "is_base", False):
        # the real shape straight away: the side outline grown and trimmed to the top outline, never a full-height block carved down
        from app.features.ext_features import ShapedBase
        label = f"{out[0].label} + {out[1].item.id} (shaped from the top and side views)"
        out[:2] = [Prepared(out[0].item, out[0].params, ShapedBase(out[0].feature, out[1].feature, label), label)]
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
_RENDER = None                       # one thread draws the pictures (matplotlib is not thread-safe)


def _render_pool():
    global _RENDER
    if _RENDER is None:
        from concurrent.futures import ThreadPoolExecutor
        _RENDER = ThreadPoolExecutor(1)
    return _RENDER


class Runner:
    agent = None                     # no SolidWorks agent unless one is given
    look_dir = None
    progress = None                  # app.runner.progress.Progress: what the SolidWorks page shows while the part is built
    _cover_ids: dict = {}
    _face_sigs: set = set()          # the faces the part had at the last measurement: a step's NEW faces are the rest
    _added_measured: dict = {}
    _unit = "mm"

    def __init__(self, sw: SolidWorksClient, *, expected_docs: tuple[str, ...] = (), log: RunLog | None = None, env_retries: int = 1,
                 agent=None, look_dir: Path | None = None, progress=None):
        self.sw, self.expected_docs, self.log, self.env_retries = sw, tuple(expected_docs), log, env_retries
        self.agent, self.look_dir = agent, look_dir                 # the SolidWorks agent (app.agent.sw_agent): reworks a step that went wrong
        self.progress = progress
        if agent is not None and progress is not None:
            agent.log.listener = self._agent_said

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
                if self.progress is not None:
                    self._show_finished_pictures()
                    if report.failure is not None:
                        self.progress.log("warn", f"Stopped at {report.failure.item_id}: {report.failure.message[:200]}")
                    self.progress.finish(report.ok)
                return report
            self._event("environment_retry", failure=report.failure.message)

    async def edit(self, bl: BuildList, prior: dict) -> RunReport:
        """The part from the last build is open in SolidWorks: the agent adds or fixes what the designer says (agent.corrections) ON it.
        Nothing is built again. `prior` = the last build's report (asdict). The steps it built are known again from the list and its
        status per step; steps the agent added then come back from their recorded params."""
        report = RunReport(bl.part_name, bl.unit, ok=True, log_dir=str(self.log.dir) if self.log else "", gaps=list(prior.get("gaps", [])),
                           warnings=list(prior.get("warnings", [])))
        report.items = [ItemReport(**i) for i in prior.get("items", [])]
        verifier = StepVerifier(self.sw, self.log)
        verifier.doc = report.document = (await verifier.current()).doc                 # the saved part, opened / active
        ctx = FeatureContext(self.sw, verifier, self.log)
        prepared = prepare(bl)
        covers = self._covers(bl, prepared)
        self._cover_ids = {p.item.id: [i.id for i in c] for p, c in zip(prepared, covers)}
        status = {i.id: i.status for i in report.items}
        built: dict[str, Any] = {}
        for p, c in zip(prepared, covers):
            if status.get(p.item.id) in ("ok", "check_failed"):
                built.update({i.id: i for i in c})
        for i in report.items:
            head = "added by the SolidWorks agent: "
            if i.id not in built and i.message.startswith(head):
                try:
                    built[i.id] = Item(id=i.id, feature=i.feature, params=json.loads(i.message[len(head):]), derived=True, note="by the SolidWorks agent")
                except (ValueError, ValidationError):                                   # a long message was cut: that step is still in the part
                    pass
        self._unit = bl.unit
        try:
            from .measure import signature
            self._face_sigs = {signature(f) for f in (await self.sw.analyze_model("faces")).analysis_json()["faces"]}
        except Exception:                                                               # noqa: BLE001 - only the drawing-vs-model table needs it
            self._face_sigs = set()
        if self.progress is not None:
            self.progress.plan(list(built.values()))
            self.progress.step(list(built), "ok")
            self.progress.log("agent", "AI agent edits the built part: " + " | ".join(self.agent.corrections)[:300])
        if await self._agent_review(bl, ctx, built, report):
            report.ok = False
        else:
            report.final = {**prior.get("final", {}), **await self._final(verifier, report)}
        if self.progress is not None:
            self.progress.finish(report.ok)
        return report

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
        covers = self._covers(bl, prepared)
        self._cover_ids = {p.item.id: [i.id for i in c] for p, c in zip(prepared, covers)}
        if self.progress is not None:
            self.progress.plan([i for c in covers for i in c])
        built: dict[str, Any] = {}                                                  # id -> the item as it stands in the part (the agent's version when it reworked one)
        self._face_sigs = set()
        self._unit = bl.unit
        for n, (p, rep) in enumerate(zip(prepared, report.items)):
            t0 = time.time()
            self._event("item_start", id=p.item.id, feature=p.item.feature)
            if self.progress is not None:
                self.progress.step(self._cover_ids.get(p.item.id, [p.item.id]), "building")
                self.progress.log("step", f"Building {' + '.join(self._cover_ids.get(p.item.id, [p.item.id]))}: {_say(p.item)}")
            mark = await self._feature_count() if n > 0 else None
            try:
                result = await p.feature.apply(ctx)
            except FeatureRequestError as ex:
                self._finish_item(rep, t0, "failed", str(ex))
                if n > 0:                                                            # not the base: skip this step, build the rest
                    report.warnings.append(f"{p.item.id} ({p.item.feature}) was not built: {ex}"[:400])
                    if await self._agent_fix(bl, ctx, built, covers[n], p, rep, mark, report):
                        return report
                    continue
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
            loose = self._made_loose_piece(result)
            if n > 0 and loose and mark is not None:
                # the step left the part in MORE pieces (a boss floating clear of the part): take it out again and build the rest on the clean part
                undo = await self.sw.call("ext_delete_after", {"count": mark})
                msg = f"removed: it made a separate piece ({loose}); undo {'ok' if undo.ok else 'FAILED: ' + undo.text[:120]}"
                self._finish_item(rep, t0, "removed", msg)
                report.warnings.append(f"NOT BUILT, it would float: {p.item.id} ({p.item.feature}): {loose}"[:400])
                if await self._agent_fix(bl, ctx, built, covers[n], p, rep, mark, report):
                    return report
                continue
            if result.ok:
                self._finish_item(rep, t0, "ok", "")
                built.update({i.id: i for i in covers[n]})
                rep.measured = await self._record(ctx, p.item, rep.verdicts, base=n == 0, params=p.params)
                await self._snapshot(n, p.item.id)
                continue
            failure = self._classify(p, result)
            if n > 0 and failure.kind != "environment":
                # A step whose check failed (e.g. a hole that overlaps a pocket removes 1.7% less) or that SolidWorks refused: keep what it
                # made, flag it for the designer to look at, and build the remaining steps. Before, one such step left a flat plate.
                self._finish_item(rep, t0, "check_failed", failure.message)
                report.warnings.append(f"CHECK FAILED, look at it in the model: {p.item.id} ({p.item.feature}): {failure.message}"[:400])
                built.update({i.id: i for i in covers[n]})
                rep.measured = await self._record(ctx, p.item, rep.verdicts)
                if await self._agent_fix(bl, ctx, built, covers[n], p, rep, mark, report):
                    return report
                continue
            report.failure = failure
            self._finish_item(rep, t0, "failed", report.failure.message)
            return report
        report.ok = True
        if await self._agent_review(bl, ctx, built, report):
            report.ok = False
            return report
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

    # ---------------------------------------------------------------- the SolidWorks agent (back and forth while building)
    @staticmethod
    def _covers(bl, prepared: list[Prepared]) -> list[list]:
        """The list items each prepared step builds (a merged step, like the shaped base, builds two)."""
        items = list(getattr(bl, "items", None) or [])
        ids = [getattr(i, "id", None) for i in items]
        if not items or any(p.item.id not in ids for p in prepared):
            return [[p.item] for p in prepared]
        starts = [ids.index(p.item.id) for p in prepared] + [len(items)]
        return [items[a:b] for a, b in zip(starts, starts[1:])]

    async def _undo_to(self, mark) -> None:
        if mark is not None:                                                            # also closes a sketch a refused feature left open
            await self.sw.call("ext_delete_after", {"count": mark})

    async def _measure(self, ctx) -> dict:
        snap = await ctx.verifier.current()
        out = {"volume_mm3": round(snap.volume * 1e9, 1), "bodies": snap.bodies}
        try:
            from app.features.base import fast_bbox
            bb = await fast_bbox(self.sw)
            if bb is not None:
                out["bbox_mm"] = [[round(v * 1000, 2) for v in bb.lo], [round(v * 1000, 2) for v in bb.hi]]
        except Exception as ex:                                                          # noqa: BLE001 - information only
            out["bbox_mm"] = f"not available: {ex}"
        return out

    async def _look(self, ctx) -> dict:
        """Export the part as it is now and draw it in the drawing's views (top, front, side, 3D)."""
        import uuid
        from app.preview import render_views_png
        folder = (self.look_dir or config.BUILD_DIR / "looks") / uuid.uuid4().hex[:8]
        if "ext_clear_selection" in (getattr(self.sw, "tool_names", None) or []):
            await self.sw.call("ext_clear_selection")
        r = await self.sw.export_document("STL", folder / "now.stl")
        await ctx.verifier.current()                                                    # back on our document (an export can activate another)
        if not r.ok or not (folder / "now.stl").is_file():
            return {"status": "failed", "message": f"could not export the part: {r.text[:200]}"}
        png = await asyncio.get_running_loop().run_in_executor(_render_pool(), render_views_png, folder / "now.stl", folder / "now.png", "the part as built now")
        if self.progress is not None:
            import shutil
            name = f"look_{folder.name}.png"
            (self.progress.folder / "live").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(png, self.progress.folder / "live" / name)
            self.progress.image(name, "AI agent's look")
        return {"status": "ok", "image": str(png), "message": "picture attached: TOP, FRONT/elevation, SIDE, 3D", **await self._measure(ctx)}

    @staticmethod
    def _observed(result) -> dict:
        """What SolidWorks really made, in the agent's words."""
        steps = []
        for v in result.verdicts:
            r = v.record()
            steps.append({"step": r["name"], "ok": r["ok"], "verdict": r["verdict"], "solidworks": str(r["message"])[:300],
                          "expected_mm3": r["expected_delta_mm3"], "measured_mm3": r["measured_delta_mm3"], "upper_bound_mm3": r["bound_mm3"],
                          "bodies_before": (r["before"] or {}).get("bodies"), "bodies_after": (r["after"] or {}).get("bodies")})
        probes = [{"check": q.name, "ok": q.ok, "detail": str(q.detail)[:200]} for q in result.probes]
        return {"steps": steps, "position_checks": probes, "notes": list(result.notes)[:5]}

    async def _usable(self, ctx, mark) -> bool:
        """The part can be measured. If not (a failed feature can leave an open sketch or a broken rebuild), everything after `mark` is
        taken out and it is measured again: False then (the step's material is gone). Still unreadable: VerifyError, the run stops."""
        try:
            await ctx.verifier.current()
            return True
        except VerifyError:
            await self._undo_to(mark)
            await ctx.verifier.current()
            return False

    async def _try_feature(self, bl, ctx, built: dict, item, mark) -> dict:
        """Build ONE item the agent asked for, on the part as it is now. Anything wrong is taken out again (the part is never left broken)."""
        try:
            feature = prepare(bl.model_copy(update={"items": [*built.values(), item]}))[-1].feature
        except RequestProblem as ex:
            return {"status": "refused", "feature_call": True, "message": f"refused before SolidWorks: {ex.message}"[:500]}
        try:
            result = await feature.apply(ctx)
        except FeatureRequestError as ex:
            await self._undo_to(mark)
            return {"status": "refused", "feature_call": True, "message": str(ex)[:500]}
        except VerifyError as ex:                                                       # SolidWorks could not measure the part after it
            await self._undo_to(mark)
            await ctx.verifier.current()                                                # still unreadable: VerifyError, the run stops
            return {"status": "failed", "feature_call": True, "message": f"SolidWorks could not analyse the part after this feature ({ex}): taken out"[:500]}
        refused = next((v for v in result.verdicts if not v.ok and v.verdict == "tool_failed"), None)
        if refused is not None:                                                         # SolidWorks refused it: whatever it left (an open sketch) comes out
            await self._undo_to(mark)
            await ctx.verifier.current()
            return {"status": "failed", "feature_call": True, "message": f"SolidWorks refused it: {refused.message}"[:500], **self._observed(result)}
        if not await self._usable(ctx, mark):
            return {"status": "failed", "feature_call": True, "message": "the part could not be analysed after this feature (an open sketch or a broken rebuild): taken out"}
        seen = {**self._observed(result), "steps_raw": [{k: v for k, v in x.record().items() if k not in ("before", "after")} for x in result.verdicts]}
        loose = self._made_loose_piece(result)
        if loose:
            await self._undo_to(mark)
            return {"status": "removed", "feature_call": True, "message": f"it made a separate floating piece ({loose}): taken out", **seen}
        if not result.ok:
            failure = self._classify(Prepared(item, None, feature, item.id), result)
            if failure.kind == "environment":
                raise VerifyError(failure.message)
            return {"status": "check_failed", "feature_call": True, "message": failure.message, **seen, "part_now": await self._measure(ctx)}
        return {"status": "ok", "feature_call": True, "message": "built and checked", **seen, "part_now": await self._measure(ctx)}

    def _tools(self, bl, ctx, built: dict, phase: str, item_id: str, mark_box: list):
        """The agent's tools, executed in SolidWorks. mark_box[0] = the feature count to go back to when a feature call is replaced / undone."""
        from app.menu.text import feature_card

        async def run(tool: str, args: dict) -> dict:
            if tool == "describe":
                name = str(args.get("feature", ""))
                return {"status": "ok", "card": feature_card(name)} if name in MENU else {"status": "refused", "message": f"no feature {name!r} on the menu"}
            if tool == "measure":
                return {"status": "ok", **await self._measure(ctx)}
            if tool == "look":
                return await self._look(ctx)
            if tool not in MENU:
                return {"status": "refused", "message": f"no tool {tool!r}: use a feature name, describe, measure or look"}
            if phase == "fix":
                await self._undo_to(mark_box[0])                                      # the call REPLACES the step that went wrong
                new_id = item_id
            else:
                mark_box[0] = await self._feature_count()
                new_id = f"A{sum(1 for k in built if str(k).startswith('A')) + 1}"
            try:
                item = Item(id=new_id, feature=tool, params=args, derived=True, note="by the SolidWorks agent")
            except ValidationError as ex:
                return {"status": "refused", "feature_call": True, "message": str(ex)[:300]}
            out = await self._try_feature(bl, ctx, {k: v for k, v in built.items() if k != new_id}, item, mark_box[0])
            if phase == "review" and out["status"] == "ok":
                self._added_measured[new_id] = await self._record(ctx, item, out.get("steps_raw", []))
            if phase == "fix":
                mark_box[1:] = [out["status"], item]                                 # what stands in the part for this step now
            if out["status"] == "ok":
                built[new_id] = item
            elif phase == "review" and out["status"] == "check_failed":
                await self._undo_to(mark_box[0])                                      # a new step that is not right is not kept
                out["message"] += " (taken out again)"
            return out
        return run

    async def _agent_fix(self, bl, ctx, built: dict, covers: list, p: Prepared, rep: ItemReport, mark, report: RunReport) -> bool:
        """True = SolidWorks is not usable any more: the run stops (report.failure says why)."""
        if self.agent is None or mark is None or not hasattr(bl, "model_copy"):
            return False
        try:
            await self._agent_fix_step(bl, ctx, built, covers, p, rep, mark, report)
            return False
        except (McpToolTimeout, VerifyError, ConnectionError) as ex:
            report.failure = Failure(p.item.id, p.item.feature, "environment", f"while the SolidWorks agent reworked it: {type(ex).__name__}: {ex}"[:400])
            return True

    async def _agent_fix_step(self, bl, ctx, built: dict, covers: list, p: Prepared, rep: ItemReport, mark, report: RunReport) -> None:
        """A step went wrong: the agent gets what SolidWorks made and reworks the step with its tools."""
        if not await self._usable(ctx, mark):                                           # what the failed step left could not even be measured: taken out
            rep.message = (rep.message + " | it left the part unreadable: taken out")[:600]
            rep.status = "failed" if rep.status == "check_failed" else rep.status
            for i in covers:
                built.pop(i.id, None)
        from app.menu.text import feature_card
        problem = {"item": p.item.model_dump(), "status": rep.status, "solidworks_said": rep.message, "steps": rep.verdicts,
                   "position_checks": rep.probes, "feature_card": feature_card(p.item.feature) if p.item.feature in MENU else "",
                   "built_before_it": [{"id": i.id, "feature": i.feature} for i in built.values() if i.id not in {c.id for c in covers}],
                   "part_now": await self._measure(ctx)}
        warn = len(report.warnings) - 1
        t0 = time.time() - rep.seconds
        if self.progress is not None:
            self.progress.step(self._cover_ids.get(p.item.id, [p.item.id]), "fixing", rep.message)
            self.progress.log("agent", f"AI agent is fixing {p.item.id}: SolidWorks said: {rep.message[:200]}")
        box = [mark, rep.status, p.item]                                                # [feature count before the step, its status in the part, the item that made it]
        end = await self.agent.fix(problem, self._tools(bl, ctx, built, "fix", p.item.id, box))
        why = str((end.get("args") or {}).get("reason") or end.get("why") or "")[:200]
        if (end.get("result") or {}).get("status") in ("ok", "check_failed") and box[2] is not p.item:
            rep.measured = await self._record(ctx, box[2], end["result"].get("steps_raw", []))
        if (end.get("result") or {}).get("status") == "ok":
            report.warnings.pop(warn)
            self._finish_item(rep, t0, "ok", f"reworked by the SolidWorks agent as {end['tool']}: {why}"[:400])
            report.warnings.append(f"REWORKED by the SolidWorks agent: {p.item.id} is now {end['tool']} {json.dumps(end.get('args'))}"[:400])
            for i in covers[1:]:
                built.pop(i.id, None)
        elif end.get("tool") == "keep" and box[1] == "check_failed":                   # built, its check failed, the agent says the shape is right
            if box[2] is not p.item:
                rep.measured = await self._record(ctx, box[2], [])                                                    # it is the agent's version of the step that stands
                built[p.item.id] = box[2]
                for i in covers[1:]:
                    built.pop(i.id, None)
                rep.message = f"reworked by the SolidWorks agent as {box[2].feature} {json.dumps(box[2].params)}"[:300]
            self._finish_item(rep, t0, "check_failed", (rep.message + f" | kept by the SolidWorks agent: {why}")[:600])
            report.warnings[warn] = f"CHECK FAILED, kept by the SolidWorks agent: {p.item.id}: {why}"[:400]
        else:
            await self._undo_to(mark)                                                   # whatever is left of this step comes out
            for i in covers:
                built.pop(i.id, None)
            if rep.status == "check_failed":
                self._finish_item(rep, t0, "removed", rep.message)
            rep.message = (rep.message + f" | the SolidWorks agent could not fix it: {why}")[:600]
        report.agent = {"turns": self.agent.log.turns, "calls": self.agent.log.calls}

    async def _agent_review(self, bl, ctx, built: dict, report: RunReport) -> bool:
        """True = SolidWorks is not usable any more (report.failure says why)."""
        if self.agent is None or not hasattr(bl, "model_copy") or "ext_feature_count" not in (getattr(self.sw, "tool_names", None) or []):
            return False
        self._review_box = [None]
        self._added_measured = {}
        try:
            await self._agent_review_part(bl, ctx, built, report)
            return False
        except (McpToolTimeout, VerifyError, ConnectionError) as ex:
            # every list step is built: a failed review call is taken out and the part is kept (the user's part was lost this way)
            try:
                await self._undo_to(self._review_box[0])
                await ctx.verifier.current()
            except (McpToolTimeout, VerifyError, ConnectionError):
                report.failure = Failure("-", "review", "environment", f"during the SolidWorks agent's review: {type(ex).__name__}: {ex}"[:400])
                return True
            report.warnings.append(f"SolidWorks agent review stopped ({type(ex).__name__}: {str(ex)[:150]}); its last call was taken out, the part is kept"[:400])
            return False

    async def _agent_review_part(self, bl, ctx, built: dict, report: RunReport) -> None:
        """Every step is done: the agent looks at the part beside the drawing and adds what is missing."""
        from app.menu.text import feature_card
        likely = {*(i.feature for i in bl.items), "hole", "cut", "profile_cut", "side_cut", "circle_boss", "fillet_chamfer_edges", "drill"}
        state = {"steps": [{"id": i.id, "feature": i.feature, "status": i.status, "message": i.message[:200]} for i in report.items],
                 "items_in_the_part": sorted(built),                        # every list item that stands in the part (a merged step builds two: F1 + E1)
                 "list": [i.model_dump() for i in bl.items], "warnings": report.warnings[-10:], "part_now": await self._measure(ctx),
                 "feature_cards": {n: feature_card(n) for n in sorted(likely) if n in MENU}}   # read here: no describe call (a turn) needed for these
        before = set(built)
        if self.progress is not None:
            self.progress.data["current"] = "review"
            self.progress.log("agent", "AI agent compares the part with the drawing")
        first_call = len(self.agent.log.calls)
        end = await self.agent.review(state, self._tools(bl, ctx, built, "review", "-", self._review_box))
        added = [k for k in built if k not in before]
        if not added and self.agent.corrections:
            # the model's own summary is not trusted for what was built: an edit that added nothing says so, with why each try failed
            tries = [c for c in self.agent.log.calls[first_call:] if c["tool"] in MENU]
            why = "; ".join(f"{c['tool']}: {str((c['result'] or {}).get('message', ''))[:120]}" for c in tries[-4:])
            end = {**end, "args": {"summary": f"Nothing was added. {len(tries)} feature tr{'y' if len(tries) == 1 else 'ies'}, none built"
                                              + (f" ({why})" if why else "") + ".",
                                   "agent_said": (end.get("args") or {}).get("summary") or (end.get("args") or {}).get("reason")}}
        for new_id in added:
            item = built[new_id]
            report.items.append(ItemReport(new_id, item.feature, "ok", message=f"added by the SolidWorks agent: {json.dumps(item.params)}"[:400],
                                           measured=self._added_measured.get(new_id, {})))
            if self.progress is not None:
                self.progress.add_step(item, "ok", "added by the AI agent")
        if end.get("tool") != "done":
            report.warnings.append(f"SolidWorks agent review ended: {(end.get('args') or {}).get('reason', end.get('tool'))}"[:300])
        report.agent = {"turns": self.agent.log.turns, "calls": self.agent.log.calls, "review": end.get("args"), "added": added}

    async def _feature_count(self):
        if "ext_feature_count" not in (getattr(self.sw, "tool_names", None) or []):
            return None
        r = await self.sw.call("ext_feature_count")
        try:
            return int(r.json()["count"]) if r.ok else None
        except (ValueError, KeyError, TypeError):
            return None

    @staticmethod
    def _made_loose_piece(result) -> str:
        """'' or why: the part has more separate bodies after the step than before it (checked for EVERY feature, whatever it expects)."""
        steps = [v for v in result.verdicts if getattr(v, "before", None) is not None and getattr(v, "after", None) is not None]
        if not steps:
            return ""
        before, after = steps[0].before.bodies, steps[-1].after.bodies
        return f"{before} body before, {after} after" if before >= 1 and after > before else ""

    def _finish_item(self, rep: ItemReport, t0: float, status: str, message: str) -> None:
        rep.status, rep.message, rep.seconds = status, message, time.time() - t0
        self._event("item_end", id=rep.id, status=status, message=message, seconds=round(rep.seconds, 2))
        if self.progress is not None:
            self.progress.step(self._cover_ids.get(rep.id, [rep.id]), status, message)
            if status == "ok":
                self.progress.log("ok", f"{rep.id} done in {rep.seconds:.0f} s" + (f": {message}" if message else ""))
            else:
                self.progress.log("warn", f"{rep.id} {status.replace('_', ' ')}: {message}")

    # ---------------------------------------------------------------- live progress (the SolidWorks page)
    def _agent_said(self, entry: dict) -> None:
        r = entry.get("result") or {}
        what = {"look": "looks at the part beside the drawing", "measure": "measures the part", "describe": f"reads the {(entry.get('args') or {}).get('feature', '')} card",
                "keep": "keeps the step as it is", "skip": "gives up on this step", "done": "finishes the review"}.get(entry["tool"], f"tries {entry['tool']}")
        where = f" {entry['item']}" if entry.get("item") not in (None, "-") else ""
        tail = f" -> {r.get('status')}: {str(r.get('message', ''))[:160]}" if r else ""
        self.progress.log("agent", f"AI agent{where} {what}" + (f" ({entry['why']})" if entry.get("why") else "") + tail)

    async def _record(self, ctx, item, verdicts: list[dict], base: bool = False, params=None) -> dict:
        """Drawing vs model for one step: the faces it added (read from SolidWorks) against the item's numbers. Never stops a build."""
        try:
            from app.features.base import fast_bbox
            from .measure import signature, step_measurement
            faces = (await self.sw.analyze_model("faces")).analysis_json()["faces"]
            sigs = {signature(f) for f in faces}
            new = [f for f in faces if signature(f) not in self._face_sigs]
            self._face_sigs = sigs
            bbox = size = None
            if base:
                bb = await fast_bbox(self.sw)
                bbox = [[v * 1000 for v in bb.lo], [v * 1000 for v in bb.hi]] if bb is not None else None
                if params is not None:
                    (w, h), _, t = base_info(params, self._unit)
                    size = (w, h, t)
            return step_measurement(item, self._unit, new, verdicts, bbox, size)
        except Exception as ex:                                                          # noqa: BLE001 - the comparison is information
            return {"error": f"{type(ex).__name__}: {ex}"[:200]}

    async def _snapshot(self, n: int, step_id: str) -> None:
        """A picture of the part after a step, drawn by a separate low-priority process: drawn on a thread of this process it held the
        interpreter and every SolidWorks call of the build waited (measured: the clamp took 830 s instead of 180 s)."""
        if self.progress is None:
            return
        self._show_finished_pictures()
        try:
            stl = self.progress.folder / "live" / f"s{n:02d}.stl"
            if "ext_clear_selection" in (getattr(self.sw, "tool_names", None) or []):
                await self.sw.call("ext_clear_selection")
            r = await self.sw.export_document("STL", stl)
            if r.ok and stl.is_file():
                import subprocess
                import sys
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
                proc = subprocess.Popen([sys.executable, "-c", "import sys; from app.preview import render_stl_png; render_stl_png(*sys.argv[1:4])",
                                         str(stl), str(stl.with_suffix(".png")), f"after {step_id}"], cwd=str(config.PROJECT_ROOT),
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
                self._renders = [*getattr(self, "_renders", []), (proc, stl.with_suffix(".png"), step_id)]
        except Exception:                                                                # noqa: BLE001 - a picture is never worth stopping a build
            pass

    def _show_finished_pictures(self) -> None:
        """The newest picture whose drawing has finished goes on the page."""
        pending = []
        for proc, png, step_id in getattr(self, "_renders", []):
            if proc.poll() is None:
                pending.append((proc, png, step_id))
            elif png.is_file():
                self.progress.image(png.name, step_id)
        self._renders = pending

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
