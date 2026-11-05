"""Live uncached R1/R2 trials; timings include ingestion, OCR and S2 persistence."""
import argparse
from dataclasses import asdict
from datetime import datetime
import io
import json
from pathlib import Path
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PIL import Image
from app import config, trace
from app.agent.orchestrator import Orchestrator
from app.agent.workers import ExtractionAgent
from app.ingest import load_pages
from app.llm import make_client
from app.perception.ocr import read_ocr
from app.perception.evidence import EvidenceStore
from eval.hybrid_score import score
from app.parallel import ordered_map


def trial(path, strategy, out, reference=None, budget=40, transport="verbose"):
    started=time.monotonic(); rid=uuid.uuid4().hex[:12]
    folder=out/f"{path.stem}-{strategy}-{rid}";folder.mkdir(parents=True)
    report={"id":rid,"input":str(path),"strategy":strategy,"budget_seconds":budget,
            "provider":config.WORKER_LLM_PROVIDER,"effort":config.EXTRACTION_REASONING_EFFORT,"model_cache":False,"transport":transport}
    try:
        with trace.run(rid,folder/"trace"):
            pages=load_pages([path])
            source={"id":rid,"pages":[{"no":p.no,"source":path.name,"source_page":p.source_page,"sha":p.sha} for p in pages]}
            payloads=[]; images={}; reads=[]
            client=make_client(config.WORKER_LLM_PROVIDER,cache=False,reasoning_effort=config.EXTRACTION_REASONING_EFFORT)
            supervisor=Orchestrator(extraction=ExtractionAgent(client))
            def read_page(page):
                ocr=read_ocr(page.png)
                (folder/f"ocr-{page.no}.json").write_text(json.dumps(ocr,ensure_ascii=False),encoding="utf-8")
                return supervisor.read_hybrid(page.no,page.png,ocr,strategy,transport)
            # Match the web application's bounded concurrency and ordered output.
            page_reads=ordered_map(read_page,pages)
            for page,read in zip(pages,page_reads):
                payloads.append(read.evidence);reads.append(asdict(read));images[f"page{page.no}"]=page.png
                with Image.open(io.BytesIO(page.png)) as original:
                    for view in read.views:
                        buffer=io.BytesIO();original.crop(view.box).save(buffer,"PNG")
                        images[f"page{page.no}.view{view.no}"]=buffer.getvalue()
            (folder/"source.json").write_text(json.dumps(source),encoding="utf-8")
            (folder/"readings.json").write_text(json.dumps(reads,ensure_ascii=False),encoding="utf-8")
            store=EvidenceStore(out/"evidence")
            binding=store.publish(source,{"pages":payloads,"review":"pending","cad_ready":False},images)
            store.load(source,binding)
            report.update(status="finished",binding=binding,pages=len(pages),
                          calls=sum(r["calls"] for r in reads),ocr_seconds=sum(p["ocr"]["seconds"] for p in payloads),
                          questions=[q for p in payloads for q in p["issues"]],
                          tokens={key:sum(a["usage"].get(key,0) for p in payloads for a in p["attempts"])
                                  for key in ("prompt_tokens","output_tokens","thought_tokens")})
            report["provider_seconds"]=round(sum(a["seconds"] for p in payloads for a in p["attempts"]),3)
            report["provider_call_seconds"]=[round(a["seconds"],3) for p in payloads for a in p["attempts"]]
            report["wire_formats"]=sorted({a.get("wire_format","verbose") for p in payloads for a in p["attempts"]})
            if reference and len(payloads)==1:
                if reference.get("source_sha") and reference["source_sha"]!=pages[0].sha:
                    raise ValueError("Reference image hash differs from the actual input")
                report["score"]=score(reference,payloads[0]["extraction"])
    except Exception as error:
        report.update(status="failed",error=f"{type(error).__name__}: {error}")
    report["seconds"]=round(time.monotonic()-started,3)
    report["timing_scope"]="whole_input_including_ingestion_ocr_validation_and_evidence_persistence"
    report["within_budget"]=(report["seconds"]<=budget if report["status"]=="finished" else False) if report.get("pages",1)==1 else None
    report["accuracy_certified"]=False
    (folder/"report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    return report


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument("inputs",nargs="+")
    parser.add_argument("--strategies",nargs="+",choices=["r1","r2"],default=["r1","r2"])
    parser.add_argument("--reference",type=Path)
    parser.add_argument("--budget",type=float,default=40)
    parser.add_argument("--out",type=Path)
    parser.add_argument("--repeat",type=int,default=1)
    parser.add_argument("--transport",choices=["verbose","anchored","lean","split"],default="verbose")
    parser.add_argument("--warm-ocr",action="store_true",help="Preinitialize OCR, matching an idle web server; no drawing/result cache")
    args=parser.parse_args()
    if not 1<=args.repeat<=10: parser.error("--repeat must be between 1 and 10")
    out=args.out or ROOT/"eval/runs"/("hybrid-"+datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True,exist_ok=True)
    reference=json.loads(args.reference.read_text(encoding="utf-8")) if args.reference else None
    reports=[]
    if args.warm_ocr:
        from app.perception.ocr import prewarm_ocr
        from app.perception import hybrid
        prewarm_ocr()
        make_client(config.WORKER_LLM_PROVIDER,cache=False,reasoning_effort=config.EXTRACTION_REASONING_EFFORT)
    for iteration in range(1,args.repeat+1):
        for index,value in enumerate(args.inputs):
            for strategy in (args.strategies if (index+iteration)%2 else list(reversed(args.strategies))):
                print(json.dumps({"event":"started","input":value,"strategy":strategy,"iteration":iteration}),flush=True)
                report=trial(Path(value).resolve(),strategy,out,reference,args.budget,args.transport)
                report.update(iteration=iteration,ocr_preinitialized=args.warm_ocr)
                reports.append(report)
                (out/"results.json").write_text(json.dumps(reports,indent=2,ensure_ascii=False),encoding="utf-8")
                print(json.dumps(report,ensure_ascii=True),flush=True)
    print(str(out),flush=True)


if __name__=="__main__": main()
