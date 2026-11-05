"""Open an existing live trial in the local review UI without another provider call."""
import argparse
import io
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PIL import Image
from app.cache import default_cache
from app.drawing.model import reconcile
from app.perception.evidence import EvidenceStore
from app.web import server


def import_trial(folder):
    folder=Path(folder)
    report=json.loads((folder/"report.json").read_text(encoding="utf-8"))
    if report["status"]!="finished": raise ValueError("Only finished trials can be imported")
    source=json.loads((folder/"source.json").read_text(encoding="utf-8"))
    reads=json.loads((folder/"readings.json").read_text(encoding="utf-8"))
    rid=source["id"]
    if not server._ID.fullmatch(rid): raise ValueError("Invalid trial id")
    target=server.RESULTS/f"{rid}.json"
    if target.exists(): raise ValueError("A result with this id already exists; it will not be overwritten")
    old_store=EvidenceStore(folder.parent/"evidence")
    evidence=old_store.load(source,report["binding"])
    retained={label:(old_store.root/"blobs"/sha).read_bytes() for label,sha in evidence["assets"].items()}
    pages=[]
    for original,read in zip(source["pages"],reads):
        png=retained[f"page{original['no']}"]
        with Image.open(io.BytesIO(png)) as image: width,height=image.size
        sha=default_cache().put_blob(png)
        pages.append({**original,"width":width,"height":height,"sha":sha,"boxes":[v["box"] for v in read["views"]],
                      "region_source":"agent","read":read})
    result={"id":rid,"llm":report["provider"],"seconds":report["seconds"],"pages":pages,"calls":report["calls"],
            "tokens":{"prompt":report["tokens"]["prompt_tokens"],"output":report["tokens"]["output_tokens"],"cached_calls":0},
            "extraction_strategy":report["strategy"],"extraction_status":"needs_attention" if report["questions"] else "complete",
            "evidence_review":"pending","extracted_features":[r["evidence"]["features"] for r in reads],
            "trial_provenance":{"path":str(folder.resolve()),"original_result":source["id"],"uncached":True}}
    model=reconcile(result);result["drawing_model"]=model.model_dump();result["review_hash"]=model.digest()
    EvidenceStore(server.EVIDENCE).publish(result,evidence["payload"],retained)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("x",encoding="utf-8") as file: json.dump(result,file,ensure_ascii=False)
    shutil.copytree(folder/"trace",server.RUNS/rid,dirs_exist_ok=False)
    return f"http://127.0.0.1:8765/#{rid}"


if __name__=="__main__":
    parser=argparse.ArgumentParser(__doc__);parser.add_argument("trial",type=Path)
    print(import_trial(parser.parse_args().trial))
