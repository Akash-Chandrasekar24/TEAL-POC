r"""Local drawing workspace: extraction -> human review -> native build -> comparison -> confirmed export.

  .venv\Scripts\python -m app.web.server            then open http://127.0.0.1:8765

Standard library only, bound to 127.0.0.1 (this machine). The browser sends the files as base64 JSON, so no form parsing is needed.
Page pictures are served from the blob store; every result is saved in storage\web\<id>.json and reopens at http://127.0.0.1:8765/#<id>.
Model calls go through the cache: reading the same files again costs nothing.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
import time
import uuid
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import config, trace
from app.parallel import ordered_map
from app.cache import default_cache
from app.ingest import load_pages
from app.perception.segment import SEGMENT_V, segment_page
from app.drawing.model import DrawingModel, Resolutions, reconcile
from app.drawing.confidence import review_index,rubric_spec
from app.agent.orchestrator import Orchestrator
from app.agent.workers import ExtractionAgent, SolidWorksAgent
from app.menu import parse_reply
from app.runner import validate_list
from app.llm.base import LLMError
from app.assembly.assets import import_model
from app.assembly.jobs import write_json
from app.assembly.model import AssemblyPlan, digest, validate_plan
from app.drawing.readiness import readiness, source_quality
from .workflow import Workflow

HERE = Path(__file__).parent
RESULTS = config.PROJECT_ROOT / "storage" / "web"
UPLOADS = config.PROJECT_ROOT / "storage" / "uploads"
RUNS = config.PROJECT_ROOT / "storage" / "runs"                   # the trace of every run (app.trace)
PLANS = config.PROJECT_ROOT / "storage" / "plans"
ASSEMBLIES = config.PROJECT_ROOT / "storage/assembly/drafts"
MODELS = config.PROJECT_ROOT / "storage/assembly/models"
READINESS = config.PROJECT_ROOT / "storage/readiness"
CHATS = config.PROJECT_ROOT / "storage/chat"
ENRICHMENTS = config.PROJECT_ROOT / "storage/extraction"
EVIDENCE = config.PROJECT_ROOT / "storage/evidence"
_ENRICH_BOOT = uuid.uuid4().hex
_ENRICH_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="drawing-extraction")
_PLAN_LOCK = threading.Lock()  # one local planning request at a time; SolidWorks is never started here
_FILE = re.compile(r"^[0-9]{3}_[A-Za-z0-9._-]+$")
MAX_BODY = 60 * 1024 * 1024                    # 6 drawing files of a few MB each, base64-encoded
MAX_FILES = 6
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[0-9a-f]{12}$")


class _Counting:
    """Passes model calls through and adds up their tokens (a cache hit adds none)."""

    def __init__(self, inner):
        self.inner, self.model = inner, inner.model
        self.provider = getattr(inner, "provider", None)
        self.cache_settings = getattr(inner, "cache_settings", {})
        self.prompt = self.output = self.cached = 0
        self.calls = 0
        self._lock = threading.Lock()

    def generate_json(self, system, parts):
        with self._lock:
            self.calls += 1
        resp = self.inner.generate_json(system, parts)
        with self._lock:
            self.prompt += resp.usage.get("prompt_tokens", 0)
            self.output += resp.usage.get("output_tokens", 0)
            self.cached += bool(resp.usage.get("cached"))
        return resp

    def discard(self, system, parts):
        getattr(self.inner, "discard", lambda *a: None)(system, parts)


def providers() -> dict:
    return {"domo": bool(config.DOMO_INSTANCE and config.domo_token()), "gemini": bool(__import__("os").getenv("GEMINI_API_KEY", "").strip())}


def run(rid: str, files: list[dict], llm_name: str | None, read: bool, *, workflow: bool = False, progressive: bool = False, on_progress=None, strategy="legacy") -> dict:
    """Save the uploads, make pages, find the view boxes, and (read=True) read them with the model. -> the result shown on the page.
    Everything is traced to storage/runs/<rid>/ (also when it fails: the log says where and why)."""
    if not files or len(files) > MAX_FILES:
        raise ValueError(f"send 1 to {MAX_FILES} files")
    if strategy not in {"legacy", "r1", "r2"} or (strategy != "legacy" and not read):
        raise ValueError("Choose legacy, r1 or r2 extraction with model reading enabled")
    if read:
        llm_name = llm_name or config.WORKER_LLM_PROVIDER
    with trace.run(rid, RUNS / rid, save_files=config.TRACE_FILES, files=[str(f.get("name")) for f in files], llm=llm_name, read=read) as tr:
        folder = UPLOADS / rid
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for i, f in enumerate(files, 1):
            name = _SAFE.sub("_", Path(str(f.get("name") or f"file{i}")).name)[:80] or f"file{i}"
            path = folder / f"{i}_{name}"
            try:
                path.write_bytes(base64.b64decode(f.get("data") or "", validate=True))
            except ValueError:
                raise ValueError(f"{name}: the upload is not valid base64") from None
            paths.append(path)
        started = time.monotonic()
        cache = default_cache()
        pages = load_pages(paths, cache=cache, tenant=config.TENANT)            # PDF pages at 300 DPI; pictures as they are
        if on_progress:
            on_progress({"event":"started", "id":rid, "total":len(pages)})
        llm = None
        supervisor = None
        if read:
            from app.llm import make_client
            llm = _Counting(make_client(llm_name, reasoning_effort=config.EXTRACTION_REASONING_EFFORT))
            supervisor = Orchestrator(extraction=ExtractionAgent(llm))
            tr.event("run", "model", provider=llm_name, model=llm.model, cache=config.LLM_CACHE)
        def read_one(page):
            cache.put_blob(page.png)                                              # served by /api/page/<sha>
            agent_views = read and (workflow or strategy != "legacy")
            boxes = [] if agent_views else [b.box for b in segment_page(page.png, page.sha, cache=cache, tenant=config.TENANT) if b.kind == "view"]
            item = {"no": page.no, "source": Path(page.source).name.split("_", 1)[-1], "source_page": page.source_page,
                    "width": page.width, "height": page.height, "sha": page.sha, "boxes": boxes, "read": None}
            if llm is not None:
                if strategy != "legacy":
                    from app.perception.ocr import read_ocr
                    item["read"] = asdict(supervisor.read_hybrid(page.no, page.png, read_ocr(page.png), strategy))
                else:
                    item["read"] = asdict(supervisor.read_page(page.no, page.png, None if agent_views else boxes))
                if agent_views or strategy != "legacy":
                    item["boxes"] = [view["box"] for view in item["read"]["views"]]
                    item["region_source"] = "agent"
            tr.event("run", "page ready", page=page.no, pages=len(pages))
            if on_progress:
                on_progress({"event":"page", "id":rid, "page":item, "total":len(pages)})
            return item
        out_pages = ordered_map(read_one, pages, workers=config.PAGE_WORKERS)
        result = {"id": rid, "llm": llm_name if read else None, "seconds": round(time.monotonic() - started, 1), "pages": out_pages,
                  "segmentation_version": SEGMENT_V,
                  "calls": sum((p["read"] or {}).get("calls", 0) for p in out_pages),
                  "tokens": {"prompt": llm.prompt, "output": llm.output, "cached_calls": llm.cached} if llm else None}
        result["source_quality"] = [{"page": p.no, **source_quality(p.png)} for p in pages]
        seen_pages = {}
        result["duplicate_pages"] = []
        for p in pages:
            if p.sha in seen_pages:
                result["duplicate_pages"].append({"page": p.no, "same_as": seen_pages[p.sha]})
            seen_pages.setdefault(p.sha, p.no)
        if read:
            result["measurement_review_policy"]="agent-confidence-v1"
            result["confidence_policy"]=rubric_spec()
            result["drawing_model"] = reconcile(result).model_dump()
            result["measurement_review"]=review_index(result,DrawingModel.model_validate(result["drawing_model"]))
            result["review_hash"] = DrawingModel.model_validate(result["drawing_model"]).digest()
            if strategy != "legacy":
                result["extraction_strategy"] = strategy
                result["extracted_features"] = [p["read"]["evidence"]["features"] for p in out_pages]
                result["extraction_status"] = "needs_attention" if any(p["read"]["issues"] for p in out_pages) else "complete"
                result["evidence_review"] = "pending"
                result["calls"] = llm.calls
            if workflow and not progressive and strategy == "legacy":
                # Feature observations belong to extraction and are shown before
                # the CAD worker gets the reviewed evidence.
                try:
                    pictures = {p.no: p.png for p in pages}
                    observed = supervisor.interpret(DrawingModel.model_validate(result["drawing_model"]), pictures)
                    result["extracted_features"] = [item.model_dump() for item in observed]
                except (LLMError, ValueError) as ex:
                    result["feature_extraction_error"] = str(ex)[:500]
                result["calls"] = llm.calls
                result["tokens"] = {"prompt": llm.prompt, "output": llm.output, "cached_calls": llm.cached}
                result["seconds"] = round(time.monotonic() - started, 1)
            result["harness"] = supervisor.harness.snapshot()
        tr.event("run", "summary", pages=len(out_pages), view_boxes=sum(len(p["boxes"]) for p in out_pages), calls=result["calls"],
                 tokens=result["tokens"], dims=sum(len(p["read"]["dims"]) for p in out_pages if p["read"]))
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / f"{rid}.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        if strategy != "legacy":
            from app.perception.evidence import EvidenceStore
            import io
            from PIL import Image
            images = {}
            for page, item in zip(pages, out_pages):
                images[f"page{page.no}"] = page.png
                with Image.open(io.BytesIO(page.png)) as image:
                    for view in item["read"]["views"]:
                        buffer = io.BytesIO()
                        image.crop(view["box"]).save(buffer, "PNG")
                        images[f"P{page.no}.v{view['no']}"] = buffer.getvalue()
            binding = EvidenceStore(EVIDENCE).publish(result,
                {"pages":[p["read"]["evidence"] for p in out_pages],"human_review":"pending","cad_ready":False}, images)
            # Binding is response-only. The immutable original result remains unchanged.
            return {**result, "evidence_binding":binding, "full_extraction_seconds":round(time.monotonic()-started,3)}
        if workflow and read and progressive:
            pending = {"source_hash": digest(result), "boot": _ENRICH_BOOT, "extraction_status": "processing"}
            write_json(ENRICHMENTS / f"{rid}.json", pending)
            _ENRICH_POOL.submit(_enrich_result, rid, llm_name, pending)
            return {**result, "extraction_status": "processing"}
        if workflow and read and any((p.get("read") or {}).get("title_block", {}).get("document_kind") in {"assembly", "detail_sheet", "mixed"} for p in out_pages):
            try:
                result = {**result, "assembly": assembly_read_result(rid, llm_name, client=llm)}
            except (LLMError, ValueError) as ex:
                result = {**result, "assembly_extraction_error": str(ex)[:500]}
    return result


def _enrich_result(rid, provider, pending):
    """Complete feature/BOM observations without modifying immutable source or human reviews."""
    started = time.monotonic()
    report = dict(pending)
    try:
        source = assembly_source(rid)
        from app.llm import make_client
        worker = Orchestrator(extraction=ExtractionAgent(make_client(provider, reasoning_effort=config.EXTRACTION_REASONING_EFFORT)))
        pictures = {p["no"]: default_cache().get_blob(p["sha"]) for p in source["pages"]}
        if any(value is None for value in pictures.values()):
            raise ValueError("The original drawing image is unavailable")
        attempt = uuid.uuid4().hex[:12]
        report["extraction_run_id"] = attempt
        with trace.run(attempt, RUNS / attempt, result_id=rid, task="feature_extraction"):
            report["extracted_features"] = [p.model_dump() for p in worker.interpret(reconcile(source), pictures)]
            if any((p.get("read") or {}).get("title_block", {}).get("document_kind") in {"assembly", "detail_sheet", "mixed"} for p in source["pages"]):
                assembly_read_result(rid, provider)
        report["extraction_status"] = "complete"
    except Exception as ex:
        report["extraction_status"] = "needs_attention"
        report["feature_extraction_error"] = f"{type(ex).__name__}: {str(ex)[:400]}"
    finally:
        report["extraction_seconds"] = round(time.monotonic() - started, 1)
        write_json(ENRICHMENTS / f"{rid}.json", report)


def recent_results(limit: int = 40) -> list[dict]:
    """Saved drawing results, newest first: what the 'Recent drawings' panel lists."""
    out = []
    for path in sorted(RESULTS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not _ID.fullmatch(str(data.get("id", ""))):
            continue
        pages = data.get("pages") or []
        sources = sorted({str(p.get("source", "")) for p in pages if p.get("source")})      # already the uploaded file name
        out.append({"id": data["id"], "name": ", ".join(sources) or data["id"], "pages": len(pages),
                    "readings": sum(len((p.get("read") or {}).get("dims", [])) for p in pages),
                    "saved": path.stat().st_mtime, "status": data.get("extraction_status") or "read"})
    return out


def result_with_design(rid: str) -> dict:
    if not _ID.fullmatch(rid):
        raise ValueError("invalid drawing result id")
    result = json.loads((RESULTS / f"{rid}.json").read_text(encoding="utf-8"))
    original_hash = digest(result)
    if result.get("extraction_strategy") in {"r1", "r2"}:
        from app.perception.evidence import EvidenceStore, digest as evidence_digest
        store = EvidenceStore(EVIDENCE)
        evidence = store.load(result)
        result["evidence_binding"] = {"revision":evidence["revision"],"sha256":evidence_digest(evidence),
                                      "source_result_hash":evidence["source_result_hash"]}
    result["drawing_model"] = reconcile(result).model_dump()
    latest = PLANS / rid / "latest.json"
    if latest.is_file():
        artifact = json.loads(latest.read_text(encoding="utf-8"))
        if artifact["model"]["source_hash"] == result["drawing_model"]["source_hash"]:
            if artifact.get("plan", {}).get("status") == "needs_review" and not artifact["plan"].get("draft"):
                # a saved REVIEW (no plan, nothing approved or built): recount its open questions with the current rules.
                # Planned artifacts stay pinned: the build step checks their model hash.
                fresh = reconcile(result, Resolutions.model_validate(artifact["model"]["resolutions"]))
                artifact = {**artifact, "model": fresh.model_dump(), "review_hash": fresh.digest(),
                            "plan": {**artifact["plan"], "questions": fresh.issues}}
            result["design"] = artifact
            result["drawing_model"] = artifact["model"]
    scope = READINESS / rid / "latest.json"
    if scope.is_file():
        assessment = json.loads(scope.read_text(encoding="utf-8"))
        if assessment["source_hash"] == digest({"pages": result["pages"]}):
            result["readiness"] = assessment
    assembly_latest = ASSEMBLIES / rid / "latest.json"
    if assembly_latest.is_file():
        saved = json.loads(assembly_latest.read_text(encoding="utf-8"))
        result["assembly"] = assembly_artifact(rid, saved["id"])
    result["review_hash"] = DrawingModel.model_validate(result["drawing_model"]).digest()
    result["measurement_review"]=review_index(result,DrawingModel.model_validate(result["drawing_model"]))
    result["confidence_policy"]=rubric_spec()
    extra = ENRICHMENTS / f"{rid}.json"
    if extra.is_file():
        observed = json.loads(extra.read_text(encoding="utf-8"))
        if observed.get("source_hash") == original_hash:
            if observed.get("extraction_status") == "processing" and observed.get("boot") != _ENRICH_BOOT:
                observed.update(extraction_status="needs_attention", feature_extraction_error="Feature extraction was interrupted. Re-extract the drawing to continue.")
            for key in ("extracted_features", "feature_extraction_error", "extraction_status", "extraction_seconds", "extraction_run_id"):
                if key in observed:
                    result[key] = observed[key]
    return result


def reread_result(rid, new_id, llm_name=None):
    source = assembly_source(rid)
    originals = UPLOADS / rid
    files = []
    if originals.is_dir():
        files = [{"name": path.name.split("_", 1)[-1], "data": base64.b64encode(path.read_bytes()).decode("ascii")}
                 for path in sorted(originals.iterdir()) if path.is_file()]
    if not files:
        for page in source["pages"]:
            data = default_cache().get_blob(page["sha"])
            if data is None:
                raise ValueError("The saved source image is unavailable. Upload the original drawing again.")
            files.append({"name": f"page_{page['no']}.png", "data": base64.b64encode(data).decode("ascii")})
    return run(new_id, files, llm_name or source.get("llm"), True, workflow=True, progressive=True)


def review_result(rid, source_hash, resolutions):
    source = assembly_source(rid)
    model = reconcile(source, resolutions)
    if model.source_hash != source_hash:
        raise ValueError("The source reading changed. Reopen it before saving corrections.")
    attempt = uuid.uuid4().hex[:12]
    artifact = {"id": attempt, "result_id": rid, "llm": source.get("llm"), "model": model.model_dump(), "review_hash": model.digest(), "measurement_review":review_index(source,model), "seconds": 0,
                "plan": {"status": "needs_review", "questions": model.issues, "draft": None, "provenance": {}, "calls": 0, "approved": False}}
    write_json(PLANS / rid / f"{attempt}.json", artifact)
    write_json(PLANS / rid / "latest.json", artifact)
    return artifact


def chat_history(rid):
    source_hash = reconcile(assembly_source(rid)).source_hash
    path = CHATS / rid / "messages.json"
    history = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    return [entry for entry in history if entry.get("source_hash") == source_hash]


def chat_reply(rid, source_hash, model_hash, target, text, *, measurement=None, view=None):
    """Apply explicit chat replies to the existing review layer, never to CAD.

    This acknowledges a saved human correction; planning and native approval
    remain separate actions. No conversational model invents numeric values.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError("Enter a reply of up to 4000 characters")
    result = result_with_design(rid)
    model = DrawingModel.model_validate(result["drawing_model"])
    if model.source_hash != source_hash or model.digest() != model_hash:
        raise ValueError("The drawing review changed. Refresh it before sending this reply.")
    resolutions = model.resolutions.model_dump()
    text = text.strip()
    if target == "note":
        note = "\n".join(value for value in (resolutions["note"], text) if value)
        if len(note) > 4000:
            raise ValueError("The combined clarification is too long. Edit the drawing clarification in the review.")
        resolutions["note"] = note
        answer = "Your clarification is saved for the next planning step. Continue from the measurement review when you are ready."
    elif target in {d.id for d in model.dimensions}:
        resolutions["dimensions"] = [d for d in resolutions["dimensions"] if d["id"] != target]
        resolutions["dimensions"].append({"id": target, "confirmed": True, "text": text})
        answer = f"Saved {target} as {text}. Check the updated reading in the workspace, then continue the review."
    else:
        questions = {f"P{p['no']}.u{i}": question for p in result["pages"] for i, question in enumerate((p.get("read") or {}).get("issues", []), 1)}
        if target not in questions:
            raise ValueError("Choose a current measurement or drawing question to reply to")
        existing = next((c for c in resolutions["clarifications"] if c["id"] == target), None)
        clarification = existing.copy() if existing else {"id": target, "text": None, "views": []}
        clarification["answer"] = text
        if measurement is not None:
            if not isinstance(measurement, str) or not measurement.strip():
                raise ValueError("Enter the corrected printed measurement")
            clarification.update(text=measurement.strip(), views=[view] if view else [])
        resolutions["clarifications"] = [c for c in resolutions["clarifications"] if c["id"] != target] + [clarification]
        answer = f"Saved your source clarification for {target}. Continue from the measurement review; any remaining questions will stay visible."
    corrected = Resolutions.model_validate(resolutions)
    candidate = reconcile(assembly_source(rid), corrected)
    if target in {d.id for d in model.dimensions}:
        # An arbitrary sentence must not become a confirmed numeric measurement.
        next(d for d in candidate.dimensions if d.id == target).nominal()
    history = chat_history(rid)
    artifact = review_result(rid, source_hash, corrected)
    entry = {"id": uuid.uuid4().hex[:12], "source_hash": source_hash, "target": target, "review_id": artifact["id"],
             "reply": text, "measurement": measurement, "view": view, "acknowledgement": answer}
    history.append(entry)
    write_json(CHATS / rid / "messages.json", history)
    return {"entry": entry, "history": history, "review": artifact, "review_hash": candidate.digest()}


def chat_question(rid,source_hash,model_hash,text,target="note"):
    from app.drawing.conversation import answer_question
    result=result_with_design(rid)
    model=DrawingModel.model_validate(result["drawing_model"])
    if model.source_hash!=source_hash or model.digest()!=model_hash:
        raise ValueError("The drawing review changed. Refresh it before asking this question.")
    response=answer_question(result,model,text,target)
    history=chat_history(rid)
    entry={"id":uuid.uuid4().hex[:12],"kind":"question","source_hash":source_hash,"model_hash":model_hash,
           "target":target,"reply":text.strip(),"acknowledgement":response["answer"],
           "citations":response["citations"],"scope":response["scope"]}
    history.append(entry)
    write_json(CHATS/rid/"messages.json",history)
    return {"entry":entry,"history":history,"review_hash":model.digest()}


def clarify_suggestions(rid: str, llm_name=None, client=None) -> dict:
    """AI-drafted answers to the open clarification questions. Proposals only: nothing is saved until the reviewer accepts each one."""
    from app.drawing.clarify import open_questions
    from app.llm import make_client
    result = result_with_design(rid)
    model = DrawingModel.model_validate(result["drawing_model"])
    questions = open_questions(result, model)
    if not questions:
        return {"id": None, "result_id": rid, "source_hash": model.source_hash, "model_hash": model.digest(), "suggestions": [], "calls": 0}
    attempt = uuid.uuid4().hex[:12]
    with trace.run(attempt, RUNS / attempt, save_files=config.TRACE_FILES, workflow="clarification_suggestions", drawing_result=rid):
        supervisor = Orchestrator(extraction=ExtractionAgent(client if client is not None else make_client(llm_name or result.get("llm") or config.WORKER_LLM_PROVIDER,
                                                                                                          reasoning_effort=config.EXTRACTION_REASONING_EFFORT)))
        out, calls = [], 0
        try:
            for page in model.pages:
                mine = [q for q in questions if q["page"] == page.no]
                if not mine:
                    continue
                png = default_cache().get_blob(next(p["sha"] for p in result["pages"] if p["no"] == page.no))
                if png is None:
                    raise ValueError(f"Page {page.no}: original page is unavailable")
                found = supervisor.suggest_clarifications(page.no, png, mine, [v.model_dump() for v in page.views],
                                                          [d.effective_text for d in model.dimensions if d.page == page.no])
                out.extend({**s, "page": page.no} for s in found)
                calls += 1
        except LLMError as ex:
            ex.run_id = attempt
            raise
    return {"id": attempt, "result_id": rid, "source_hash": model.source_hash, "model_hash": model.digest(), "suggestions": out, "calls": calls,
            "note": "Proposals only. Nothing is saved until you accept each one."}


def assess_result(rid: str, llm_name=None, client=None) -> dict:
    from app.llm import make_client
    source = assembly_source(rid)
    attempt = uuid.uuid4().hex[:12]
    with trace.run(attempt, RUNS / attempt, save_files=config.TRACE_FILES, workflow="reconstruction_readiness", drawing_result=rid):
        supervisor = Orchestrator(extraction=ExtractionAgent(client if client is not None else make_client(llm_name or source.get("llm") or config.WORKER_LLM_PROVIDER, reasoning_effort=config.EXTRACTION_REASONING_EFFORT)))
        assessments, qualities = [], []
        try:
            for page in source["pages"]:
                png = default_cache().get_blob(page["sha"])
                if png is None:
                    raise ValueError(f"Page {page['no']}: original page is unavailable")
                qualities.append(source_quality(png))
                assessments.append(supervisor.assess_page(png))
        except LLMError as ex:
            ex.run_id = attempt
            raise
        artifact = {"id": attempt, "result_id": rid, "source_hash": digest({"pages": source["pages"]}),
                    "assessments": [a.model_dump() for a in assessments], "report": readiness(assessments, qualities),
                    "harness": supervisor.harness.snapshot()}
        write_json(READINESS / rid / f"{attempt}.json", artifact)
        write_json(READINESS / rid / "latest.json", artifact)
        return artifact


def plan_result(rid: str, source_hash: str, resolutions: Resolutions, llm_name: str | None = None, client=None) -> dict:
    """Plan a saved read; only corrections are accepted from the browser, never CAD params.

    Immutable attempts retain evidence and proposed geometry. latest.json is an
    atomic pointer snapshot. A source hash prevents applying reviews to changed
    drawings. Creating a plan never starts a job or bypasses its approval pause.
    """
    if not _ID.fullmatch(rid):
        raise ValueError("invalid drawing result id")
    result = json.loads((RESULTS / f"{rid}.json").read_text(encoding="utf-8"))
    if result.get("extraction_strategy") in {"r1", "r2"}:
        from app.perception.evidence import EvidenceStore
        EvidenceStore(EVIDENCE).load(result)
        raise ValueError("R1/R2 are extraction evaluation modes. Their evidence is pending engineering acceptance; CAD execution remains available through the existing reviewed extraction workflow.")
    model = reconcile(result, resolutions)
    if any((p.get("read") or {}).get("title_block", {}).get("document_kind") in {"assembly", "detail_sheet", "mixed"} for p in result["pages"]):
        raise ValueError("The source reader identified multiple components. Use separate component plans and the assembly workflow before creating a native 2D sheet.")
    if model.source_hash != source_hash:
        raise ValueError("The source reading changed. Reopen the result and review it again.")
    scope_path = READINESS / rid / "latest.json"
    if scope_path.is_file():
        scope = json.loads(scope_path.read_text(encoding="utf-8"))
        if scope["source_hash"] != digest({"pages": result["pages"]}):
            raise ValueError("The reconstruction assessment is stale; reassess the source")
        if any(a["document_kind"] in {"assembly", "detail_sheet", "mixed"} for a in scope["assessments"]):
            raise ValueError("This source contains an assembly or multiple detail parts. Use reviewed component plans/models and the assembly workflow; the single-part compiler cannot reconstruct the whole sheet.")
    attempt = uuid.uuid4().hex[:12]
    started = time.monotonic()
    with trace.run(attempt, RUNS / attempt, save_files=config.TRACE_FILES, drawing_result=rid, workflow="planning") as tr:
        llm = execution_llm = None
        supervisor = None
        pictures = {}
        try:
            if not model.issues:
                from app.llm import make_client
                provider = llm_name or result.get("llm") or config.WORKER_LLM_PROVIDER
                llm = _Counting(client if client is not None else make_client(provider, reasoning_effort=config.EXTRACTION_REASONING_EFFORT))
                # Injection retains the old single scripted client for callers/tests.
                # Production workers have independent clients using the same provider.
                execution_llm = llm if client is not None else _Counting(make_client(provider, reasoning_effort=config.CAD_REASONING_EFFORT))
                cache = default_cache()
                for page in model.pages:
                    picture = cache.get_blob(page.sha)
                    if picture is not None:
                        pictures[page.no] = picture
            supervisor = Orchestrator(ExtractionAgent(llm), SolidWorksAgent(execution_llm))
            report = supervisor.plan(model, pictures)
        except LLMError as ex:
            ex.run_id = attempt
            raise
        except ValueError as ex:
            # Malformed or ungrounded proposals are review failures, never lists.
            tr.warn("plan", "proposal refused", reason=str(ex))
            report = {"version": "1", "model_hash": model.digest(), "status": "needs_information",
                      "questions": [f"The proposed plan was refused: {ex}"], "draft": None, "approved": False,
                      "provenance": {}}
        counters = [] if llm is None else [llm] if execution_llm is llm else [llm, execution_llm]
        report["calls"] = sum(c.calls for c in counters)
        artifact = {"id": attempt, "result_id": rid, "llm": llm_name or result.get("llm") or config.WORKER_LLM_PROVIDER,
                    "model": model.model_dump(), "review_hash": model.digest(), "measurement_review":review_index(result,model), "plan": report,
                    "seconds": round(time.monotonic() - started, 1),
                    "tokens": {"prompt": sum(c.prompt for c in counters), "output": sum(c.output for c in counters),
                               "cached_calls": sum(c.cached for c in counters)} if counters else None}
        artifact["harness"] = supervisor.harness.snapshot() if supervisor is not None else None
        folder = PLANS / rid
        folder.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(artifact, indent=2, ensure_ascii=False)
        (folder / f"{attempt}.json").write_text(encoded, encoding="utf-8")
        temporary = folder / f"{attempt}.tmp"
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, folder / "latest.json")
        tr.event("plan", "saved", result_id=rid, status=report["status"], calls=report["calls"])
    return artifact


def downloadable_list(rid: str, attempt: str | None = None) -> dict:
    result = result_with_design(rid)
    artifact = result.get("design")
    if attempt is not None:
        if not _ID.fullmatch(attempt):
            raise ValueError("invalid planning attempt id")
        artifact = json.loads((PLANS / rid / f"{attempt}.json").read_text(encoding="utf-8"))
        if artifact["result_id"] != rid:
            raise ValueError("planning attempt belongs to another drawing")
    if not artifact or artifact["plan"]["status"] != "ready_for_approval":
        raise ValueError("Resolve the drawing questions before downloading a build list")
    model = reconcile(result, Resolutions.model_validate(artifact["model"]["resolutions"]))
    if model.source_hash != artifact["model"]["source_hash"] or model.digest() != artifact["plan"]["model_hash"] or model.issues:
        raise ValueError("The plan is stale; review and generate it again")
    draft = artifact["plan"]["draft"]
    reasons = validate_list(parse_reply(draft))
    if reasons:
        raise ValueError("The draft failed validation: " + "; ".join(reasons))
    return draft


def assembly_source(rid: str) -> dict:
    if not _ID.fullmatch(rid):
        raise ValueError("invalid drawing result id")
    return json.loads((RESULTS / f"{rid}.json").read_text(encoding="utf-8"))


def assembly_artifact(rid: str, attempt: str) -> dict:
    if not _ID.fullmatch(attempt):
        raise ValueError("invalid assembly attempt id")
    source = assembly_source(rid)
    artifact = json.loads((ASSEMBLIES / rid / f"{attempt}.json").read_text(encoding="utf-8"))
    plan = AssemblyPlan.model_validate(artifact["plan"])
    if artifact["result_id"] != rid or plan.source_hash != digest(source):
        raise ValueError("The drawing source changed; reread and review the BOM")
    # Asset availability is checked again, rather than reusing an old status.
    artifact["blockers"] = validate_plan(plan, MODELS)
    artifact["status"] = "needs_information" if artifact["blockers"] else "ready_for_approval"
    return artifact


def save_assembly(rid: str, plan: AssemblyPlan, extracted: dict, harness: dict | None = None, review_notes=None) -> dict:
    attempt = uuid.uuid4().hex[:12]
    blockers = validate_plan(plan, MODELS)
    artifact = {"id": attempt, "result_id": rid, "plan": plan.model_dump(), "extracted": extracted,
                "status": "needs_information" if blockers else "ready_for_approval", "blockers": blockers, "approved": False}
    if harness is not None:
        artifact["harness"] = harness
    if review_notes:
        artifact["review_notes"] = review_notes
    write_json(ASSEMBLIES / rid / f"{attempt}.json", artifact)
    write_json(ASSEMBLIES / rid / "latest.json", artifact)
    return artifact


def assembly_read_result(rid: str, llm_name=None, client=None) -> dict:
    from app.llm import make_client
    source = assembly_source(rid)
    attempt = uuid.uuid4().hex[:12]
    with trace.run(attempt, RUNS / attempt, save_files=config.TRACE_FILES, drawing_result=rid, workflow="assembly_extraction") as tr:
        llm = _Counting(client if client is not None else make_client(llm_name or source.get("llm") or config.WORKER_LLM_PROVIDER, reasoning_effort=config.EXTRACTION_REASONING_EFFORT))
        pictures = {p["no"]: picture for p in source["pages"] if (picture := default_cache().get_blob(p["sha"])) is not None}
        supervisor = Orchestrator(extraction=ExtractionAgent(llm))
        try:
            plan = supervisor.read_bom(source, pictures)
        except LLMError as ex:
            ex.run_id = attempt
            raise
        artifact = save_assembly(rid, plan, plan.model_dump(), supervisor.harness.snapshot())
        tr.event("assembly", "saved", attempt=artifact["id"], status=artifact["status"])
        return artifact


def assembly_review_result(rid: str, attempt: str, edited: dict, review_notes=None) -> dict:
    latest = ASSEMBLIES / rid / "latest.json"
    if latest.is_file() and json.loads(latest.read_text(encoding="utf-8"))["id"] != attempt:
        raise ValueError("The assembly review changed in another request. Reopen the latest review before saving.")
    prior = assembly_artifact(rid, attempt)
    plan = AssemblyPlan.model_validate(edited)
    if plan.source_hash != prior["plan"]["source_hash"]:
        raise ValueError("Reviewed assembly belongs to different drawing evidence")
    if review_notes is not None and (not isinstance(review_notes, list) or len(review_notes) > 200 or any(not isinstance(n, str) or not n.strip() or len(n) > 2000 for n in review_notes)):
        raise ValueError("Assembly clarification notes must be nonempty source corrections")
    return save_assembly(rid, plan, prior["extracted"], prior.get("harness"), [*prior.get("review_notes", []), *(review_notes or [])])


PART_LISTS = config.PROJECT_ROOT / "storage/parts"


def parts_overview(rid: str) -> dict:
    """Parts on the sheet, each with its latest build list, and whether SolidWorks can build here."""
    from app.web.parts import detect_parts, latest_lists, solidworks_status
    result = result_with_design(rid)
    lists = latest_lists(PART_LISTS, rid)
    parts = [{**{k: v for k, v in p.items() if k != "views"}, "views": [{"page": v["page"], "no": v["no"], "label": v["label"]} for v in p["views"]],
              "list": lists.get(p["id"])} for p in detect_parts(result)]
    batch = load_batch(rid)
    if batch and batch["phase"] in ("preparing", "building") and batch["id"] not in _ACTIVE:
        batch["phase"] = "interrupted"                                            # the server restarted mid-run: offer to start again
    if batch:                                                                     # live status of every part job the batch started
        for item in batch["items"]:
            if item.get("job_id"):
                try:
                    item["job"] = WORKFLOW.status(item["job_id"])
                except (OSError, ValueError):
                    pass
    assembly = None
    latest_asm = ASSEMBLIES / rid / "latest.json"
    if latest_asm.is_file():
        assembly = json.loads(latest_asm.read_text(encoding="utf-8"))
        if (batch or {}).get("assembly", {}).get("job_id"):
            try:
                assembly["job"] = WORKFLOW.status(batch["assembly"]["job_id"])
            except (OSError, ValueError):
                pass
    return {"result_id": rid, "parts": parts, "solidworks": solidworks_status(), "batch": batch, "assembly": assembly}


# ---------------- "Build everything": prepare every build list, ONE human approval, then build part after part
_BATCH_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cad-batch")       # one SolidWorks session: one build at a time
_PREP_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="build-lists")      # AI work for other drawings must not wait behind a build
_BATCH_LOCK = threading.Lock()
_PREP_WORKERS = 4                         # build lists are independent model calls (45-135 s each): write them side by side
_ACTIVE = set()                           # batch ids running in THIS server process; a batch left "running" by a restart is interrupted


def _batch_path(rid):
    if not _ID.fullmatch(rid):
        raise ValueError("invalid drawing result id")
    return PART_LISTS / rid / "batch.json"


def load_batch(rid):
    path = _batch_path(rid)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _save_batch(rid, batch):
    batch["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_json(_batch_path(rid), batch)


def start_batch(rid, llm_name=None):
    """Step 1 of "Build everything": write a build list for every part that has none (AI), then wait for the human's single approval."""
    from app.web.parts import detect_parts, latest_lists
    result = result_with_design(rid)
    if any("human review required" in q or "confirm this" in q for q in result["drawing_model"].get("issues", [])):
        raise ValueError("Approve the measurements on the Review step first")
    with _BATCH_LOCK:
        current = load_batch(rid)
        if current and current.get("phase") in ("preparing", "building") and current["id"] in _ACTIVE:
            return current
        lists = latest_lists(PART_LISTS, rid)
        items = [{"part_id": p["id"], "name": p["name"], "status": "listed" if lists.get(p["id"]) else "to_prepare"} for p in detect_parts(result)]
        batch = {"id": uuid.uuid4().hex[:12], "result_id": rid, "phase": "preparing", "items": items, "llm": llm_name}
        _save_batch(rid, batch)
        _ACTIVE.add(batch["id"])
    _PREP_POOL.submit(_prepare_batch, rid, batch["id"], llm_name)
    return batch


_DOUBTS_ONLY = re.compile(r"^needs_human is not empty|^uncertain items")


def _only_doubts(art) -> bool:
    """The list has 3D steps and nothing blocks it but the AI's questions / assumed sizes: confirming it is enough."""
    return bool(art and art["draft"].get("items")) and art["check"]["status"] == "needs_information" \
        and all(_DOUBTS_ONLY.search(b) for b in art["check"]["blocking"])


def _ai_decides(art) -> list[dict]:
    return [{"question": q, "answer": "AI decides"} for q in (art or {}).get("draft", {}).get("needs_human", [])]


def resolve_with_ai(rid, part_ids, llm_name=None):
    """The designer handed every doubt of these parts to the AI. A list that only waits on its questions / assumed sizes is confirmed at once
    (no model call); the others are rewritten by the AI in the background, side by side, and confirmed as written. The build still waits for
    the designer's approval."""
    from app.web.parts import detect_parts, latest_lists
    result = result_with_design(rid)
    lists = latest_lists(PART_LISTS, rid)
    known = {p["id"]: p for p in detect_parts(result)}
    wanted = [pid for pid in part_ids if pid in known]
    if not wanted:
        raise ValueError("None of those parts is on this drawing")
    rewrite = []
    for pid in wanted:
        art = lists.get(pid)
        if _only_doubts(art):
            confirm_part_list(rid, pid, _ai_decides(art))
        elif not (art and art["check"]["status"] == "ready"):
            rewrite.append(pid)
    with _BATCH_LOCK:
        current = load_batch(rid)
        if current and current.get("phase") in ("preparing", "building") and current["id"] in _ACTIVE:
            raise ValueError("Build lists are being written already: wait for them to finish")
        if not rewrite:
            batch = {**(current or {"id": uuid.uuid4().hex[:12], "result_id": rid, "items": [], "llm": llm_name}), "phase": "awaiting_approval"}
            batch["items"] = [{"part_id": p["id"], "name": p["name"], "status": "listed"} for p in known.values()]
            _save_batch(rid, batch)
            return batch
        items = [{"part_id": p["id"], "name": p["name"], **({"status": "to_prepare", "ai_decides": True, "answers": _ai_decides(lists.get(p["id"]))}
                                                             if p["id"] in rewrite else {"status": "listed"})} for p in known.values()]
        batch = {"id": uuid.uuid4().hex[:12], "result_id": rid, "phase": "preparing", "items": items, "llm": llm_name}
        _save_batch(rid, batch)
        _ACTIVE.add(batch["id"])
    _PREP_POOL.submit(_prepare_batch, rid, batch["id"], llm_name)
    return batch


def _prepare_batch(rid, bid, llm_name):
    batch = load_batch(rid)

    def one(item):
        with _BATCH_LOCK:
            item["status"] = "preparing"
            _save_batch(rid, batch)
        try:
            if item.get("ai_decides"):                                           # the designer handed this part's doubts to the AI
                art = create_part_list(rid, item["part_id"], llm_name, answers=item.get("answers"), work_it_out=True)
                wrong = [b for b in art["check"]["blocking"] if not _DOUBTS_ONLY.search(b)]
                if wrong or not art["draft"].get("items"):                       # the checker refused the geometry: one retry with its words
                    fix = [{"question": "The offline checker refused your last build list. Fix exactly this and keep everything else",
                            "answer": "; ".join(wrong) or "the list had no 3D steps"}]
                    art = create_part_list(rid, item["part_id"], llm_name, answers=(item.get("answers") or []) + fix, work_it_out=True)
                if _only_doubts(art):
                    confirm_part_list(rid, item["part_id"], _ai_decides(art))
            else:
                create_part_list(rid, item["part_id"], llm_name)
            done = {"status": "listed"}
        except Exception as ex:                                                  # noqa: BLE001 - recorded per part; the batch goes on
            done = {"status": "list_failed", "detail": f"{type(ex).__name__}: {ex}"}
        with _BATCH_LOCK:
            item.update(done)
            _save_batch(rid, batch)
    try:
        with ThreadPoolExecutor(max_workers=_PREP_WORKERS, thread_name_prefix="build-list") as pool:
            list(pool.map(one, [it for it in batch["items"] if it["status"] == "to_prepare"]))
        with _BATCH_LOCK:
            batch["phase"] = "awaiting_approval"
            _save_batch(rid, batch)
    finally:
        _ACTIVE.discard(bid)


def approve_batch(rid, bid, accepted):
    """Step 2: the human approved the summary (one click). Build every ready part, one after the other. accepted: {part_id: [gap ids]}."""
    from app.web.parts import latest_lists, solidworks_status
    with _BATCH_LOCK:
        batch = load_batch(rid)
        if not batch or batch["id"] != bid or batch["phase"] != "awaiting_approval":
            raise ValueError("This build summary changed. Refresh it before approving.")
        sw = solidworks_status()
        if not sw["ready"]:
            raise ValueError(f"SolidWorks is not ready: {sw['detail']}")
        lists = latest_lists(PART_LISTS, rid)
        for item in batch["items"]:
            art = lists.get(item["part_id"])
            if not art:
                continue
            if art["check"]["status"] != "ready":
                item.update(status="not_buildable", detail="; ".join(art["check"]["blocking"]))
                continue
            gaps = [u["id"] for u in art["check"]["unsupported"]]
            if set(gaps) - set((accepted or {}).get(item["part_id"], [])):
                item.update(status="not_approved", detail="Its not-modelled items were not accepted")
                continue
            item.update(status="queued", list_id=art["id"], accepted_gaps=gaps)
        batch["phase"] = "building"
        _save_batch(rid, batch)
        _ACTIVE.add(bid)
    _BATCH_POOL.submit(_build_batch, rid, bid)
    return batch


def _build_batch(rid, bid):
    try:
        _build_each(rid)
    finally:
        _ACTIVE.discard(bid)


def _build_each(rid):
    batch = load_batch(rid)
    for item in batch["items"]:
        if item["status"] != "queued":
            continue
        item["status"] = "building"
        _save_batch(rid, batch)
        try:
            job = WORKFLOW.create(rid, item["list_id"])
            job = WORKFLOW.action(job["id"], job["revision"], "build", accepted_gaps=item["accepted_gaps"])
            item.update(status=job["phase"], job_id=job["id"], detail=job.get("detail") or "")
        except Exception as ex:                                                  # noqa: BLE001
            item.update(status="build_failed", detail=f"{type(ex).__name__}: {ex}")
        _save_batch(rid, batch)
    batch["phase"] = "done"
    _save_batch(rid, batch)


def assemble_parts(rid: str, llm_name=None, client=None) -> dict:
    """"Assemble into one file": the parts the last batch built become the components; the AI drafts where each copy goes from the assembly
    views. Saved as the drawing's assembly plan, NOTHING confirmed: the designer approves it (approve_assembly) before it is built."""
    from app.assembly.assets import import_model
    from app.assembly.from_parts import built_part, draft_placements, plan_from_parts
    from app.llm import make_client
    from app.web.parts import assembly_views, detect_parts, part_sheet
    result = result_with_design(rid)
    batch = load_batch(rid) or {}
    known = {p["id"]: p for p in detect_parts(result)}
    built, left_out = [], []
    for item in batch.get("items", []):
        found = built_part(WORKFLOW.root / "jobs", item["job_id"]) if item.get("job_id") else None
        part = known.get(item["part_id"])
        if not (found and part):
            left_out.append(f"{item['name']}: {'not built' if part else 'not on the sheet'}")
            continue
        asset = import_model(found["path"].name, found["path"].read_bytes(), MODELS)
        qty = part.get("quantity") if isinstance(part.get("quantity"), int) and part["quantity"] > 0 else 1
        built.append({"id": part["id"], "name": part["name"], "item": part.get("item", ""), "quantity": qty, "bbox_mm": found["bbox_mm"],
                      "sha256": asset["sha256"], "source_name": asset["source_name"], "job": item["job_id"]})
    if not built:
        raise ValueError("No part has been built yet: build the parts first, then assemble them")
    views = assembly_views(result)
    if not views:
        raise ValueError("The sheet has no assembly or section view to place the parts from")
    shas = {pg["no"]: pg["sha"] for pg in result["pages"]}
    picture = part_sheet(result, {"name": "assembly", "views": views}, lambda no: default_cache().get_blob(shas[no]))
    attempt = uuid.uuid4().hex[:12]
    with trace.run(attempt, RUNS / attempt, save_files=config.TRACE_FILES, drawing_result=rid, workflow="assembly_from_parts"):
        llm = client if client is not None else make_client(llm_name or result.get("llm") or config.WORKER_LLM_PROVIDER, reasoning_effort=config.CAD_REASONING_EFFORT)
        draft = draft_placements(llm, picture, built)
    unit = (result.get("drawing_model") or {}).get("unit") if (result.get("drawing_model") or {}).get("unit") in ("mm", "inch") else "mm"
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(str(result.get("name") or rid)).stem)[:60].strip("_") or "assembly"
    plan, problems = plan_from_parts(name, digest(assembly_source(rid)), unit, built, draft)
    artifact = save_assembly(rid, plan, {"draft": draft.model_dump(), "frame": draft.frame}, review_notes=None)
    artifact.update(left_out=left_out, problems=problems, frame=draft.frame)
    write_json(ASSEMBLIES / rid / f"{artifact['id']}.json", artifact)
    write_json(ASSEMBLIES / rid / "latest.json", artifact)
    return artifact


def approve_assembly(rid: str, attempt: str) -> dict:
    """The designer approved the assembly plan: confirm it and build it in SolidWorks (background; progress in the batch file)."""
    from app.assembly.from_parts import approve
    from app.web.parts import solidworks_status
    art = assembly_artifact(rid, attempt)
    latest = json.loads((ASSEMBLIES / rid / "latest.json").read_text(encoding="utf-8"))
    if latest["id"] != attempt:
        raise ValueError("The assembly plan changed. Refresh it before approving.")
    sw = solidworks_status()
    if not sw["ready"]:
        raise ValueError(f"SolidWorks is not ready: {sw['detail']}")
    plan = approve(AssemblyPlan.model_validate(art["plan"]))
    saved = save_assembly(rid, plan, art.get("extracted", {}), art.get("harness"), art.get("review_notes"))
    for key in ("left_out", "problems", "frame"):
        if key in latest:
            saved[key] = latest[key]
    write_json(ASSEMBLIES / rid / f"{saved['id']}.json", saved)
    write_json(ASSEMBLIES / rid / "latest.json", saved)
    if saved["blockers"]:
        raise ValueError("The assembly cannot be built yet: " + "; ".join(saved["blockers"][:6]))
    with _BATCH_LOCK:
        batch = load_batch(rid) or {"id": uuid.uuid4().hex[:12], "result_id": rid, "items": [], "phase": "done"}
        batch["assembly"] = {"status": "building", "attempt": saved["id"]}
        _save_batch(rid, batch)
    _BATCH_POOL.submit(_build_assembly, rid, saved["id"])
    return saved


def _build_assembly(rid, attempt):
    try:
        job = WORKFLOW.create(rid, attempt)
        job = WORKFLOW.action(job["id"], job["revision"], "build")
        done = {"status": job["phase"], "job_id": job["id"], "detail": "; ".join(job.get("questions") or [])[:2000]}
    except Exception as ex:                                                      # noqa: BLE001 - recorded for the page
        done = {"status": "build_failed", "detail": f"{type(ex).__name__}: {ex}"[:2000]}
    with _BATCH_LOCK:
        batch = load_batch(rid)
        batch["assembly"] = {**batch.get("assembly", {}), **done}
        _save_batch(rid, batch)


def _part(result: dict, part_id: str) -> dict:
    from app.web.parts import detect_parts
    part = next((p for p in detect_parts(result) if p["id"] == part_id), None)
    if part is None:
        raise ValueError("That part is not on this drawing")
    return part


def create_part_list(rid: str, part_id: str, llm_name=None, client=None, answers=None, work_it_out=False) -> dict:
    """One model call: the part's views (or, with none of its own, the assembly views) + the reviewed measurements + the designer's answers
    -> a build list (full feature menu). work_it_out: the AI may infer unprinted sizes, each marked ASSUMED for the designer to confirm.
    Validated offline; nothing is built."""
    from app.llm import make_client
    from app.llm.base import FilePart
    from app.menu import parse_reply
    from app.web.parts import assembly_views, part_sheet, reader_context, save_part_list
    from app.agent.prompts import reader_system
    result = result_with_design(rid)
    if any("human review required" in q or "confirm this" in q for q in result["drawing_model"].get("issues", [])):
        raise ValueError("Approve the measurements on the Review step first: the build list uses the reviewed values")
    part = _part(result, part_id)
    shas = {p["no"]: p["sha"] for p in result["pages"]}
    shown = part if part["views"] else {**part, "views": assembly_views(result)}
    if not shown["views"]:
        raise ValueError(f"{part['name']} is not drawn anywhere on the sheet: describe it in an answer and let the AI work it out")
    png = part_sheet(result, shown, lambda no: default_cache().get_blob(shas[no]))
    work_it_out = work_it_out or not part["views"]                              # nothing printed for it: the AI must work its sizes out
    from app.web.parts import latest_lists
    previous = latest_lists(PART_LISTS, rid).get(part_id)
    context = reader_context(result, part, result["drawing_model"], answers, work_it_out)
    effort = config.CAD_REASONING_EFFORT
    if answers and previous and previous["draft"].get("items"):
        # An EDIT, not a new list: the earlier list + the designer's answers. Measured on a connecting rod: a full rewrite at high effort
        # thought for 14,850 tokens (220 s); writing a list from scratch at lower effort returned no 3D steps, so only edits run at medium.
        keep = {k: previous["draft"][k] for k in ("items", "seen_dimensions", "unsupported", "needs_human") if k in previous["draft"]}
        context += ("\nYour previous build list for this part is below. Apply the designer's answers to it: change only the items, numbers and "
                    "questions the answers affect, keep every other item exactly as it is, and return the COMPLETE list in the same format.\n"
                    + json.dumps(keep, ensure_ascii=False))
        effort = "medium"
    attempt = uuid.uuid4().hex[:12]
    with trace.run(attempt, RUNS / attempt, save_files=config.TRACE_FILES, workflow="part_build_list", drawing_result=rid, part=part_id):
        provider = llm_name or result.get("llm") or config.WORKER_LLM_PROVIDER
        supervisor = Orchestrator(extraction=ExtractionAgent(client if client is not None else make_client(provider, reasoning_effort=effort)))
        try:
            response = supervisor.read_list([FilePart(png, "image/png"), context])
        except LLMError as ex:
            ex.run_id = attempt
            raise
    draft = response.data if isinstance(response.data, dict) else {}
    if effort == "medium" and previous and draft.get("items"):                  # an edit: the format fields it was not shown come from the earlier list
        draft = {**{k: v for k, v in previous["draft"].items() if k not in ("accepted_gaps", "designer_answers")}, **draft}
    try:
        parse_reply(draft)
    except ValueError:
        pass                                                                    # kept and reported by the check: the reviewer sees why it is not buildable
    return save_part_list(PART_LISTS, rid, part, draft, "ai", provider)


def confirm_part_list(rid: str, part_id: str, answers=None) -> dict:
    from app.web.parts import confirm_part_list as confirm, latest_lists
    art = latest_lists(PART_LISTS, rid).get(part_id)
    if not art:
        raise ValueError("This part has no build list to confirm yet")
    return confirm(PART_LISTS, rid, _part(result_with_design(rid), part_id), art, answers)


def upload_part_list(rid: str, part_id: str, draft) -> dict:
    from app.web.parts import save_part_list
    if not isinstance(draft, dict):
        raise ValueError("A build list file is a JSON object")
    return save_part_list(PART_LISTS, rid, _part(result_with_design(rid), part_id), draft, "file")


def workflow_plan(rid, attempt):
    from app.web.parts import load_part_list
    part_list = load_part_list(PART_LISTS, rid, attempt)
    if part_list is not None:                                                    # a part's build list: the same approval / build / review path
        if part_list["check"]["status"] != "ready":
            raise ValueError("This part's build list is not buildable yet: " + "; ".join(part_list["check"]["blocking"]))
        return part_list, part_list["draft"]
    result = result_with_design(rid)
    if result.get("extraction_strategy") in {"r1", "r2"}:
        raise ValueError("R1/R2 extraction evaluation evidence cannot start or export a CAD workflow")
    assembly = result.get("assembly")
    if assembly and assembly["id"] == attempt:
        if result["drawing_model"]["issues"]:
            raise ValueError("Resolve the measurement review before starting the assembly")
        if assembly["blockers"]:
            raise ValueError("Resolve assembly questions before building: " + "; ".join(assembly["blockers"]))
        # Bind assembly execution to the separately reviewed measurement layer.
        return {**assembly, "llm": result.get("llm") or config.WORKER_LLM_PROVIDER,
                "measurement_review": result["drawing_model"]}, assembly["plan"]
    artifact = result.get("design")
    if not artifact or artifact["id"] != attempt:
        raise ValueError("The measurement review changed. Reopen the latest plan before building or exporting.")
    return {**artifact, "llm": artifact.get("llm") or result.get("llm") or config.WORKER_LLM_PROVIDER}, downloadable_list(rid, attempt)


WORKFLOW = Workflow(config.PROJECT_ROOT / "storage/workflow", workflow_plan, assets=MODELS)


@lru_cache(maxsize=16)
def _static_payload(path: Path, stamp: tuple[int, int]):
    data = path.read_bytes()
    return data, gzip.compress(data, compresslevel=6, mtime=0), hashlib.sha256(data).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def _static(self, name: str, ctype: str):
        path = HERE / name
        stat = path.stat()
        data, compressed, digest = _static_payload(path, (stat.st_mtime_ns, stat.st_size))
        use_gzip = False
        for encoding in self.headers.get("Accept-Encoding", "").lower().split(","):
            parts = [part.strip() for part in encoding.split(";")]
            if parts[0] == "gzip":
                try:
                    quality = next((float(p[2:]) for p in parts[1:] if p.startswith("q=")), 1)
                    use_gzip = quality > 0
                except ValueError:
                    pass
        tag = f'"{digest}{"-gzip" if use_gzip else ""}"'
        headers = {"ETag": tag, "Vary": "Accept-Encoding", "Cache-Control": "no-cache"}
        if self.headers.get("If-None-Match") == tag:
            self.send_response(304)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            return
        if use_gzip:
            headers["Content-Encoding"] = "gzip"
        return self._send(200, compressed if use_gzip else data, ctype, headers)

    def _send(self, code: int, body: bytes, ctype: str, headers=None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if not headers:
            self.send_header("Cache-Control", "no-store" if ctype.startswith("application/json") or self.path.startswith("/api/workflow/") else "max-age=3600")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _same_origin_json(self):
        """True for a JSON request from this page (no Origin header, or the local server's own origin)."""
        origin, port = self.headers.get("Origin"), self.server.server_port
        return self.headers.get("Content-Type", "").lower().startswith("application/json") and (not origin or origin in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"})

    def _json(self, code: int, data) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._static("index.html", "text/html; charset=utf-8")
        if path == "/assembly.html":
            return self._static("assembly.html", "text/html; charset=utf-8")
        if path == "/viewer.js":
            return self._static("viewer.js", "text/javascript; charset=utf-8")
        if path == "/solidworks.js":
            return self._static("solidworks.js", "text/javascript; charset=utf-8")
        if path == "/ui.js":
            return self._static("ui.js", "text/javascript; charset=utf-8")
        if path == "/ui.css":
            return self._static("ui.css", "text/css; charset=utf-8")
        if path == "/brand.css":
            return self._static("brand.css", "text/css; charset=utf-8")
        if path == "/gwc-logo.svg":
            return self._static("gwc-logo.svg", "image/svg+xml")
        if re.fullmatch(r"/fonts/poppins-(400|500|600|700)\.woff2", path):
            return self._static(path[1:], "font/woff2")
        if path == "/api/config":
            return self._json(200, {"providers": providers(), "default": config.WORKER_LLM_PROVIDER, "max_files": MAX_FILES,
                                    "models": {"domo": config.DOMO_MODEL, "gemini": config.GEMINI_MODEL}})
        m = re.fullmatch(r"/api/parts/([0-9a-f]{12})", path)
        if m:
            try:
                return self._json(200, parts_overview(m.group(1)))
            except (OSError, ValueError) as ex:
                return self._json(404, {"error": str(ex)})
        if path == "/api/results":
            return self._json(200, {"results": recent_results()})
        if path == "/api/harness":
            from app.agent.harness import capabilities
            return self._json(200, capabilities())
        m = re.fullmatch(r"/api/chat/([0-9a-f]{12})", path)
        if m:
            try:
                return self._json(200, chat_history(m.group(1)))
            except (ValueError, OSError) as ex:
                return self._json(409, {"error": str(ex)})
        m = re.fullmatch(r"/api/workflow/result/([0-9a-f]{12})", path)
        if m:
            try:
                return self._json(200, WORKFLOW.latest(m.group(1)))
            except (ValueError, OSError) as ex:
                return self._json(409, {"error": str(ex)})
        m = re.fullmatch(r"/api/workflow/([0-9a-f]{12})(?:/(preview|mesh|download/(SLDPRT|SLDASM|STEP|STL|BUNDLE)))?", path)
        if m:
            try:
                jid, kind, fmt = m.groups()
                if kind is None:
                    return self._json(200, WORKFLOW.status(jid))
                artifact = WORKFLOW.artifact(jid, fmt or kind)
                if kind == "mesh":
                    from app.preview import read_stl
                    import numpy as np
                    mesh = read_stl(artifact)
                    if len(mesh) > 250000 or not np.isfinite(mesh).all():
                        raise ValueError("Mesh is too large or invalid for interactive review; use the saved preview and SolidWorks")
                    return self._json(200, {"triangles": mesh.reshape(-1).round(5).tolist(), "unit": "mm"})
                return self._send(200, artifact.read_bytes(), "image/png" if kind == "preview" else "application/octet-stream")
            except (ValueError, OSError, KeyError) as ex:
                return self._json(409, {"error": str(ex)})
        m = re.fullmatch(r"/api/assembly/([0-9a-f]{12})/([0-9a-f]{12})(/plan)?", path)
        if m:
            try:
                artifact = assembly_artifact(m.group(1), m.group(2))
                return self._json(200, artifact["plan"] if m.group(3) else artifact)
            except (ValueError, OSError) as ex:
                return self._json(409, {"error": str(ex)})
        if path.startswith("/api/page/"):
            sha = path.rsplit("/", 1)[-1]
            data = default_cache().get_blob(sha) if _HEX.match(sha) else None
            if data is None and _HEX.fullmatch(sha):
                retained = EVIDENCE / "blobs" / sha
                if retained.is_file():
                    candidate = retained.read_bytes()
                    if hashlib.sha256(candidate).hexdigest() == sha:
                        data = candidate
            return self._send(200, data, "image/png") if data else self._json(404, {"error": "no such page"})
        if path.startswith("/api/result/"):
            rid = path.rsplit("/", 1)[-1]
            f = RESULTS / f"{rid}.json"
            if not _ID.fullmatch(rid) or not f.is_file():
                return self._json(404, {"error": "no such result"})
            try:
                return self._json(200, result_with_design(rid))
            except (ValueError, OSError, KeyError) as ex:
                return self._json(409, {"error":f"Saved evidence needs attention: {ex}"})
        m = re.fullmatch(r"/api/design/([0-9a-f]{12})(?:/([0-9a-f]{12}))?/list", path)
        if m:
            try:
                return self._json(200, downloadable_list(m.group(1), m.group(2)))
            except (ValueError, OSError) as ex:
                return self._json(409, {"error": str(ex)})
        if path.startswith("/api/trace/"):                                    # the run's events (also for a run that failed)
            rid = path.rsplit("/", 1)[-1]
            f = RUNS / rid / "trace.jsonl"
            if not (_ID.match(rid) and f.is_file()):
                return self._json(404, {"error": "no trace for this run"})
            return self._json(200, [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()])
        m = re.match(r"^/api/run/([0-9a-f]{12})/(file/([^/]+)|log)$", path)  # a file the model was shown / answered, or run.log
        if m:
            rid, name = m.group(1), m.group(3)
            f = RUNS / rid / ("run.log" if name is None else f"files/{name}")
            if name is not None and not _FILE.match(name) or not f.is_file():
                return self._json(404, {"error": "no such file"})
            ctype = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp", "json": "text/plain; charset=utf-8",
                     "pdf": "application/pdf"}.get(f.suffix.lstrip("."), "text/plain; charset=utf-8")
            return self._send(200, f.read_bytes(), ctype)
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/api/read", "/api/reread", "/api/chat", "/api/review", "/api/plan", "/api/assess", "/api/clarify", "/api/parts/create", "/api/parts/upload", "/api/parts/confirm", "/api/parts/ai-resolve", "/api/parts/assemble", "/api/parts/assemble-approve", "/api/parts/build-all", "/api/parts/approve-all", "/api/assembly/read", "/api/assembly/review", "/api/assembly/model", "/api/workflow/start", "/api/workflow/decision"):
            return self._json(404, {"error": "not found"})
        size = int(self.headers.get("Content-Length") or 0)
        if not 0 < size <= MAX_BODY:
            return self._json(413, {"error": f"the upload must be under {MAX_BODY // 1024 // 1024} MB"})
        rid = uuid.uuid4().hex[:12]
        try:
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("send a JSON object")
            llm = body.get("llm") if body.get("llm") in ("domo", "gemini") else None
            if self.path == "/api/chat":
                if not self._same_origin_json():
                    return self._json(403, {"error": "Send replies from this drawing workspace"})
                if not _PLAN_LOCK.acquire(blocking=False):
                    return self._json(409, {"error": "A review or plan is running. Wait for it to finish before replying."})
                try:
                    if body.get("mode")=="ask":
                        return self._json(200,chat_question(str(body.get("result_id","")),str(body.get("source_hash","")),str(body.get("model_hash","")),body.get("text"),str(body.get("target","note"))))
                    if body.get("mode","correct")!="correct": raise ValueError("Choose ask or correct chat mode")
                    return self._json(200, chat_reply(str(body.get("result_id", "")), str(body.get("source_hash", "")), str(body.get("model_hash", "")),
                                                     str(body.get("target", "note")), body.get("text"), measurement=body.get("measurement"), view=body.get("view")))
                finally:
                    _PLAN_LOCK.release()
            if self.path == "/api/reread":
                return self._json(200, reread_result(str(body.get("result_id", "")), rid, llm))
            if self.path.startswith("/api/workflow/"):
                # Native actions must be explicit same-origin JSON requests.
                if not self._same_origin_json():
                    return self._json(403, {"error": "Use this app's confirmation controls to continue the workflow"})
                if self.path.endswith("/start"):
                    job = WORKFLOW.create(str(body.get("result_id", "")), str(body.get("attempt", "")))
                    if body.get("build") is True:
                        gaps = [str(g) for g in body.get("accepted_gaps") or [] if isinstance(g, str)]
                        job = WORKFLOW.action(job["id"], job["revision"], "build", accepted_gaps=gaps)
                    return self._json(200, job)
                decision = {key: body[key] for key in ("review_hash", "compared_drawing", "checked_measurements", "message", "params", "accepted_gaps") if key in body}
                return self._json(200, WORKFLOW.action(str(body.get("job_id", "")), body.get("revision"), str(body.get("action", "")), **decision))
            if self.path in ("/api/parts/assemble", "/api/parts/assemble-approve"):
                if not self._same_origin_json():
                    return self._json(403, {"error": "Start this from the drawing workspace"})
                rid = str(body.get("result_id", ""))
                if self.path.endswith("/assemble"):
                    if not _PLAN_LOCK.acquire(blocking=False):
                        return self._json(409, {"error": "A plan is already running. Wait for it to finish."})
                    try:
                        return self._json(200, assemble_parts(rid, llm))
                    finally:
                        _PLAN_LOCK.release()
                return self._json(200, approve_assembly(rid, str(body.get("attempt", ""))))
            if self.path == "/api/parts/ai-resolve":
                if not self._same_origin_json():
                    return self._json(403, {"error": "Start this from the drawing workspace"})
                ids = body.get("part_ids") if isinstance(body.get("part_ids"), list) else []
                return self._json(200, resolve_with_ai(str(body.get("result_id", "")), [str(i) for i in ids], llm))
            if self.path in ("/api/parts/build-all", "/api/parts/approve-all"):
                if not self._same_origin_json():
                    return self._json(403, {"error": "Start builds from this drawing workspace"})
                rid = str(body.get("result_id", ""))
                if self.path.endswith("/build-all"):
                    return self._json(200, start_batch(rid, llm))
                accepted = body.get("accepted_gaps") if isinstance(body.get("accepted_gaps"), dict) else {}
                return self._json(200, approve_batch(rid, str(body.get("batch_id", "")), accepted))
            if self.path in ("/api/parts/create", "/api/parts/upload", "/api/parts/confirm"):
                if not self._same_origin_json():
                    return self._json(403, {"error": "Create build lists from this drawing workspace"})
                if not _PLAN_LOCK.acquire(blocking=False):
                    return self._json(409, {"error": "A review or plan is running. Wait for it to finish."})
                try:
                    rid, pid = str(body.get("result_id", "")), str(body.get("part_id", ""))
                    answers = body.get("answers") if isinstance(body.get("answers"), list) else None
                    if self.path.endswith("/create"):
                        return self._json(200, create_part_list(rid, pid, llm, answers=answers, work_it_out=body.get("work_it_out") is True))
                    if self.path.endswith("/confirm"):
                        return self._json(200, confirm_part_list(rid, pid, answers))
                    return self._json(200, upload_part_list(rid, pid, body.get("list")))
                finally:
                    _PLAN_LOCK.release()
            if self.path == "/api/clarify":
                if not self._same_origin_json():
                    return self._json(403, {"error": "Ask for suggestions from this drawing workspace"})
                if not _PLAN_LOCK.acquire(blocking=False):
                    return self._json(409, {"error": "A review or plan is running. Wait for it to finish."})
                try:
                    return self._json(200, clarify_suggestions(str(body.get("result_id", "")), llm))
                finally:
                    _PLAN_LOCK.release()
            if self.path == "/api/assess":
                if not _PLAN_LOCK.acquire(blocking=False):
                    return self._json(409, {"error": "A plan or assessment is already running. Wait for it to finish."})
                try:
                    return self._json(200, assess_result(str(body.get("result_id", "")), llm))
                finally:
                    _PLAN_LOCK.release()
            if self.path == "/api/review":
                if not _PLAN_LOCK.acquire(blocking=False):
                    return self._json(409, {"error": "A review is already being saved. Wait for it to finish."})
                try:
                    return self._json(200, review_result(str(body.get("result_id", "")), str(body.get("source_hash", "")), Resolutions.model_validate(body.get("resolutions") or {})))
                finally:
                    _PLAN_LOCK.release()
            if self.path == "/api/assembly/model":
                return self._json(200, import_model(str(body.get("name", "")), base64.b64decode(body.get("data", ""), validate=True), MODELS))
            if self.path in ("/api/assembly/read", "/api/assembly/review"):
                if not _PLAN_LOCK.acquire(blocking=False):
                    return self._json(409, {"error": "A plan is already running. Wait for it to finish."})
                try:
                    if self.path.endswith("/read"):
                        artifact = assembly_read_result(str(body.get("result_id", "")), llm)
                    else:
                        artifact = assembly_review_result(str(body.get("result_id", "")), str(body.get("attempt", "")), body.get("plan"), body.get("review_notes"))
                    return self._json(200, artifact)
                finally:
                    _PLAN_LOCK.release()
            if self.path == "/api/plan":
                if not _PLAN_LOCK.acquire(blocking=False):
                    return self._json(409, {"error": "A plan is already running. Wait for it to finish."})
                try:
                    artifact = plan_result(str(body.get("result_id", "")), str(body.get("source_hash", "")),
                                           Resolutions.model_validate(body.get("resolutions") or {}), llm)
                    return self._json(200, artifact)
                finally:
                    _PLAN_LOCK.release()
            if body.get("stream") is True:
                return self._read_stream(rid, body, llm)
            return self._json(200, run(rid, body.get("files") or [], llm, bool(body.get("read")), workflow=body.get("workflow") is True, progressive=body.get("progressive") is True, strategy=body.get("strategy", "legacy")))
        except (ValueError, json.JSONDecodeError) as ex:                      # bad upload: the user can fix it
            return self._json(400, {"error": str(ex), "id": rid if (RUNS / rid).is_dir() else None})
        except Exception as ex:                                               # model or file failure: show it, keep serving; the trace says why
            error_id = getattr(ex, "run_id", rid)
            return self._json(502, {"error": f"{type(ex).__name__}: {str(ex)[:300]}", "id": error_id if (RUNS / error_id).is_dir() else None})

    def _read_stream(self, rid, body, llm):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        lock = threading.Lock()
        def emit(event):
            data = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
            with lock:
                self.wfile.write(data)
                self.wfile.flush()
        try:
            result = run(rid, body.get("files") or [], llm, bool(body.get("read")), workflow=body.get("workflow") is True,
                         progressive=body.get("progressive") is True, on_progress=emit, strategy=body.get("strategy", "legacy"))
            emit({"event":"result", "result":result})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as ex:
            try:
                emit({"event":"error", "id":rid, "error":f"{type(ex).__name__}: {str(ex)[:300]}"})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    def log_message(self, fmt, *args):                                        # one short line per request
        print(f"{self.address_string()} {fmt % args}")


def main(port: int = 8765) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if os.name == "nt":
        def warm_ocr():
            from app.perception.ocr import prewarm_ocr
            try:
                prewarm_ocr()
                from app.perception import hybrid  # initialize the evidence schema before an upload
                from app.llm import make_client
                if config.WORKER_LLM_PROVIDER=="domo" and providers()["domo"]:
                    make_client("domo",reasoning_effort=config.EXTRACTION_REASONING_EFFORT)
            except Exception as error: print(f"OCR initialization deferred: {type(error).__name__}")
        threading.Thread(target=warm_ocr,daemon=True,name="ocr-startup").start()
    print(f"open http://127.0.0.1:{port}   (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    import sys
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
