"""Browser builds private review artifacts; delivery happens only after acceptance."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path

from app.drawing.sheets import sha256
from app.finish import finish_part, safe_name, step_unit
from app.mcp_client import SolidWorksClient
from app.menu import parse_reply
from app.preview import read_stl
from app.runner import Runner
from app.verify import RunLog
from .execution import cad_execution
from .guards import list_hash
from .live import apply_expected_final, keep_open_docs


async def _preview(approved, job_dir):
    plan = parse_reply(approved)
    log = RunLog(f"review_{job_dir.name}")
    async with SolidWorksClient() as sw:
        from .sw_agent import agent_for
        from app.runner.progress import Progress
        report = await Runner(sw, expected_docs=keep_open_docs(), log=log, agent=agent_for(job_dir), look_dir=job_dir / "looks",
                              progress=Progress(job_dir)).run(plan)
        finish = None
        if report.ok:
            info = Path(job_dir) / "drawing.json"
            general = json.loads(info.read_text(encoding="utf-8")).get("general_tolerance") if info.is_file() else None
            await dimension_part(sw, plan, general, report)
            finish = await _save_for_review(sw, plan, job_dir, report)
    return {"report": asdict(report), "finish": finish}


async def _save_for_review(sw, plan, job_dir, report) -> dict:
    # Private tessellation is needed to show a rotatable preview. No STEP
    # or downloadable native deliverable is produced at this stage.
    folder = job_dir / "private" / uuid.uuid4().hex[:12]
    prepared = await finish_part(sw, folder, safe_name(plan.part_name or job_dir.name), plan.unit,
                                 expected_bbox_mm=report.final.get("bbox_mm"), review_only=True)
    apply_expected_final(prepared, report.final, plan.expected_final)
    finish = asdict(prepared)
    workspace = await sw.inspect_assembly("workspace")
    if not workspace.ok:
        raise ValueError("Cannot verify the saved preview's active configuration")
    finish["configuration"] = workspace.json()["configuration"]
    return finish


async def _edit(approved, job_dir, prior, source):
    """The designer's correction, made ON the part already built (the saved SLDPRT is opened, or activated when it is still open)."""
    from .sw_agent import agent_for
    from app.runner.progress import Progress
    plan = parse_reply(approved)
    agent = agent_for(job_dir)
    if agent is None:
        raise ValueError("The SolidWorks agent is off (SW_AGENT=off) or the job has no drawing: it cannot edit the part")
    async with SolidWorksClient() as sw:
        opened = await sw.open_document(str(source))
        if not opened.ok:
            raise ValueError(f"Could not open the built part in SolidWorks: {opened.text[:300]}")
        report = await Runner(sw, log=RunLog(f"edit_{job_dir.name}"), agent=agent, look_dir=job_dir / "looks",
                              progress=Progress(job_dir)).edit(plan, prior)
        # ponytail: no new SolidWorks dimensions on an edit (the old sketches already carry theirs; dimensioning them again would add doubles)
        finish = await _save_for_review(sw, plan, job_dir, report) if report.ok else None
    return {"report": asdict(report), "finish": finish}


def edit_preview(approved, job_dir, prior, source):
    with cad_execution(Path(job_dir), "part_edit", list_hash(approved)):
        return asyncio.run(_edit(approved, Path(job_dir), prior, source))


async def dimension_part(sw, plan, general, report) -> None:
    """Real SolidWorks dimensions on the built part (lengths, diameters, radii, angles: what Smart Dimension draws), each with the drawing's
    tolerance: the callout that prints that value, else the drawing's general tolerance, else ISO 2768-m. They are added with the sketch's own
    values, so the geometry does not change: the volume is compared before and after to prove it."""
    from app.drawing.tolerance import judge
    from app.features.base import factor
    if "ext_dimension_sketches" not in (getattr(sw, "tool_names", None) or []):
        return
    try:
        before = (await sw.analyze_model("mass_properties")).text
        r = await sw.geometry("ext_dimension_sketches", {"max_segments": 40})
        if not r.ok:
            report.warnings.append(f"dimensions not added in SolidWorks: {r.text[:200]}")
            return
        after = (await sw.analyze_model("mass_properties")).text
        vol = lambda t: (re.search(r"volume=([0-9.Ee+-]+)", t) or [None, None])[1]
        if vol(before) != vol(after):
            report.warnings.append(f"adding the dimensions changed the volume ({vol(before)} -> {vol(after)} m3): check the part")
        printed = ", ".join(i.printed for i in plan.items if i.printed)
        mm = factor(plan.unit) * 1000
        tols = []
        for d in r.json().get("dimensions", []):
            if d["kind"] == "angle":
                continue
            value_mm = d["value"] * 1000
            j = judge({"what": d["kind"], "drawing": value_mm, "model": value_mm, "unit": "mm"}, printed, general, mm)
            if j.get("tol"):
                tols.append({"name": d["name"], "lo": j["tol"][0] / 1000, "hi": j["tol"][1] / 1000})
        if tols:
            await sw.geometry("ext_set_tolerances", {"dims": tols})
        report.final["dimensions_in_solidworks"] = {"added": len(r.json().get("dimensions", [])), "with_tolerance": len(tols),
                                                    "skipped_sketches": r.json().get("skipped_sketches", [])}
    except Exception as ex:                                                   # noqa: BLE001 - dimensions are information, never a reason to stop
        report.warnings.append(f"dimensions not added in SolidWorks: {type(ex).__name__}: {ex}"[:300])


def preview_build(approved, job_dir):
    with cad_execution(Path(job_dir), "part_review", list_hash(approved)):
        return asyncio.run(_preview(approved, Path(job_dir)))


def verify_receipt(job_dir, receipt):
    root = Path(job_dir).resolve()
    for item in [*receipt["files"].values(), *receipt.get("dependencies", [])]:
        path = Path(item["path"]).resolve()
        if not path.is_relative_to(root / "private") or not path.is_file() or sha256(path) != item["sha256"]:
            raise ValueError("The model/preview changed after review; generate and review it again")


async def _export(approved, job_dir, receipt):
    verify_receipt(job_dir, receipt)
    if list_hash(approved) != receipt["approved_hash"]:
        raise ValueError("The approved geometry changed after the preview was created")
    native_key = "SLDASM" if "SLDASM" in receipt["files"] else "SLDPRT"
    source = Path(receipt["files"][native_key]["path"]).resolve()
    folder = Path(job_dir) / "exports" / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True)
    outputs = {native_key: folder / source.name, "STEP": folder / (source.stem + ".step"), "STL": folder / (source.stem + ".stl")}

    async def checked(response):
        result = await response
        if not result.ok:
            raise ValueError(result.text[:500])
        return result

    async with SolidWorksClient() as sw:
        await checked(sw.ensure_ready())
        workspace = (await checked(sw.inspect_assembly("workspace"))).json()
        if workspace.get("documents") == []:
            await checked(sw.open_document(str(source)))
        for stage in ("before", "after"):
            workspace = (await checked(sw.inspect_assembly("workspace"))).json()
            if (Path(workspace.get("active_path") or "").resolve() != source
                    or workspace.get("modified") is not False
                    or workspace.get("configuration") != receipt["configuration"]):
                raise ValueError("Activate the unchanged reviewed model/configuration before exporting. Modified geometry needs a new review.")
            verify_receipt(job_dir, receipt)
            if stage == "before":
                shutil.copyfile(source, outputs[native_key])
                for dependency in receipt.get("dependencies", []):
                    relative = Path(dependency["path"]).resolve().relative_to(source.parent)
                    target = folder / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(dependency["path"], target)
                await checked(sw.export_document("STEP", outputs["STEP"]))
                await checked(sw.export_document("STL", outputs["STL"]))
    if step_unit(outputs["STEP"]) != approved["unit"]:
        raise ValueError("Exported STEP has the wrong length unit")
    if sha256(outputs[native_key]) != receipt["files"][native_key]["sha256"]:
        raise ValueError("The native deliverable differs from the confirmed model")
    mesh = read_stl(outputs["STL"]).reshape(-1, 3)
    reference = read_stl(Path(receipt["files"]["STL"]["path"])).reshape(-1, 3)
    import numpy as np
    if not np.isfinite(mesh).all() or not np.allclose(mesh.max(0)-mesh.min(0), reference.max(0)-reference.min(0), atol=.05, rtol=0):
        raise ValueError("Exported mesh dimensions differ from the reviewed model")
    if native_key == "SLDASM":
        import zipfile
        bundle = folder / (source.stem + ".zip")
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in outputs.values():
                archive.write(path, path.name)
            for dependency in receipt.get("dependencies", []):
                relative = Path(dependency["path"]).resolve().relative_to(source.parent)
                copied = folder / relative
                if sha256(copied) != dependency["sha256"]:
                    raise ValueError("Bundled component differs from the reviewed assembly")
                archive.write(copied, relative.as_posix())
        outputs["BUNDLE"] = bundle
    return {key: {"path": str(path.resolve()), "sha256": sha256(path)} for key, path in outputs.items() if path.stat().st_size > 0}


def export_reviewed(approved, job_dir, receipt):
    with cad_execution(Path(job_dir), "accepted_part_export", receipt["approved_hash"]):
        return asyncio.run(_export(approved, Path(job_dir), receipt))


def close_saved_app_documents() -> list[str]:
    """Before an assembly run (it needs an empty SolidWorks and closes nothing itself): close the documents that are files THIS APP saved
    under storage/ and are unchanged since (after a batch the last built part stays open). Anything else stays open and the run refuses."""
    try:
        import pythoncom
        import win32com.client as com
        from app import config
    except ImportError:
        return []
    pythoncom.CoInitialize()
    closed = []
    try:
        sw = com.GetActiveObject("SldWorks.Application")
        root = (config.PROJECT_ROOT / "storage").resolve()
        ours = lambda p: bool(p) and Path(p).resolve().is_relative_to(root)
        docs = list(sw.GetDocuments or [])
        parts = [d for d in docs if d.GetType == 1]                       # swDocPART: an assembly's components are open as documents too
        if parts and all(ours(d.GetPathName) for d in parts):            # so an unsaved assembly next to ONLY our parts is a failed run of ours
            for doc in docs:
                if not doc.GetPathName and doc.GetType == 2:               # swDocASSEMBLY
                    title = doc.GetTitle                                   # (read before closing: the closed document is gone)
                    sw.CloseDoc(title)
                    closed.append(title)
        for doc in list(sw.GetDocuments or []):
            path, title = doc.GetPathName, doc.GetTitle
            if ours(path) and not doc.GetSaveFlag:
                sw.CloseDoc(title)
                closed.append(title)
    except Exception:                                                    # noqa: BLE001 - the run itself reports what stays open
        pass
    return closed


def preview_assembly(approved, job_dir, assets, supervisor):
    from app.assembly.model import AssemblyPlan
    plan = AssemblyPlan.model_validate(approved)
    output = Path(job_dir) / "private" / uuid.uuid4().hex[:12]
    async def execute():
        close_saved_app_documents()
        async with SolidWorksClient() as sw:
            ready = await sw.ensure_ready()
            if not ready.ok:
                raise ValueError(ready.text)
            return await supervisor.build_assembly(sw, plan, assets, output, plan.digest(), review_only=True)
    with cad_execution(Path(job_dir), "assembly_review", plan.digest()):
        return asyncio.run(execute())
