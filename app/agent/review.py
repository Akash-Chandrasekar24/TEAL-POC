"""Browser builds private review artifacts; delivery happens only after acceptance."""
from __future__ import annotations

import asyncio
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
    return {"report": asdict(report), "finish": finish}


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


def preview_assembly(approved, job_dir, assets, supervisor):
    from app.assembly.model import AssemblyPlan
    plan = AssemblyPlan.model_validate(approved)
    output = Path(job_dir) / "private" / uuid.uuid4().hex[:12]
    async def execute():
        async with SolidWorksClient() as sw:
            ready = await sw.ensure_ready()
            if not ready.ok:
                raise ValueError(ready.text)
            return await supervisor.build_assembly(sw, plan, assets, output, plan.digest(), review_only=True)
    with cad_execution(Path(job_dir), "assembly_review", plan.digest()):
        return asyncio.run(execute())
