"""R1/R2 OCR-assisted extraction. Recognition remains separate from CAD support."""
from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass, field
from typing import Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app import trace
from app.llm.base import FilePart
from app.perception.dims import dim_tokens
from app.perception.extract import CropView, Dimension, PageRead, pack, mark_boxes
from app.perception import compact
from app.drawing.confidence import RUBRIC_PROMPT,rubric_score


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Located(Strict):
    id: str = Field(min_length=1, max_length=80)
    box: tuple[float, float, float, float]

    @field_validator("box")
    @classmethod
    def valid_box(cls, box):
        if not all(0 <= v <= 1000 for v in box) or box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError("Source box must be nonempty and normalized to 0..1000")
        return box


class ViewEvidence(Located):
    type: Literal["plan", "front", "side", "section", "detail", "iso", "other"]
    label: str = ""
    cutting_plane: str = ""
    section_evidence: str = ""
    assessed: bool = False
    questions: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def unpack_section_reference(cls,data):
        if isinstance(data,dict) and isinstance(data.get("cutting_plane"),dict):
            plane=data["cutting_plane"]
            if (set(plane)<={"label","evidence"} and isinstance(plane.get("label"),str)
                    and isinstance(plane.get("evidence",""),str) and isinstance(data.get("section_evidence",""),str)):
                data={**data,"cutting_plane":plane["label"],
                      "section_evidence":"\n".join(x for x in (data.get("section_evidence",""),plane.get("evidence","")) if x)}
        return data


class Callout(Located):
    text: str = Field(min_length=1)
    category: Literal["measurement", "symbol", "note", "material", "component"]
    views: list[str] = Field(default_factory=list)
    ocr_ids: list[str] = Field(default_factory=list)
    meaning: str = ""
    nominal: str | None = None
    unit: str = "unknown"
    quantity: int | None = Field(default=None, ge=1, strict=True)
    tolerance: str = ""
    status: Literal["matched", "vision_only", "ocr_only", "conflict", "unreadable"] = "vision_only"
    alternatives: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None,ge=0,le=100,strict=True)
    confidence_reason: str = Field(default="",max_length=500)
    confidence_metrics: tuple[int,int,int,int,int] | None = None
    confidence_support_views: list[str] = Field(default_factory=list)

    @field_validator("confidence_metrics",mode="before")
    @classmethod
    def valid_metrics(cls,value):
        if value is not None and rubric_score(value) is None: raise ValueError("Confidence requires five integer ratings from 0 to 5")
        return value

    @field_validator("confidence",mode="before")
    @classmethod
    def numeric_confidence(cls,value):
        if isinstance(value,bool): raise ValueError("Confidence must be an agent-reported number")
        return value

    @field_validator("unit", mode="before")
    @classmethod
    def unknown_unit(cls, value):
        return "unknown" if value is None else value


class Feature(Strict):
    id: str
    kind: str
    description: str
    views: list[str]
    dimensions: dict[str, list[str]] = Field(default_factory=dict)
    questions: list[str] = Field(default_factory=list)


class Symbol(Strict):
    status: Literal["present", "absent", "unreadable"] = "unreadable"
    convention: Literal["first", "third", "unknown"] = "unknown"
    evidence: str = ""


class BOMItem(Strict):
    item: str
    part_number: str = ""
    description: str
    quantity: int | None = Field(default=None, ge=1, strict=True)
    evidence: str


CATEGORIES = {"dimensions", "symbols", "projection", "sections", "features", "notes", "bom"}


class Extraction(Strict):
    name: str = ""
    unit: Literal["mm", "inch", "unknown"] = "unknown"
    projection: Literal["first", "third", "unknown"] = "unknown"
    projection_symbol: Symbol = Field(default_factory=Symbol)
    document_kind: Literal["part", "assembly", "detail_sheet", "mixed", "unknown"] = "unknown"
    views: list[ViewEvidence]
    callouts: list[Callout]
    features: list[Feature]
    bom: list[BOMItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    coverage: dict[str, Literal["assessed", "absent", "unreadable", "not_checked"]]
    whole_page_checked: bool = False

    @model_validator(mode="after")
    def validate_references(self):
        view_ids = {v.id for v in self.views}
        callout_ids = {c.id for c in self.callouts}
        if len(view_ids) != len(self.views) or len(callout_ids) != len(self.callouts):
            raise ValueError("Duplicate view or occurrence identity")
        if len({f.id for f in self.features}) != len(self.features):
            raise ValueError("Duplicate feature identity")
        for callout in self.callouts:
            if not (set(callout.views)|set(callout.confidence_support_views)) <= view_ids:
                raise ValueError("Callout references an unknown view")
        for feature in self.features:
            if not set(feature.views) <= view_ids or not {i for ids in feature.dimensions.values() for i in ids} <= callout_ids:
                raise ValueError("Feature references unknown evidence")
        if set(self.coverage) - CATEGORIES:
            raise ValueError("Unknown coverage category")
        return self


PROMPT = '''Read this engineering drawing AND the positioned OCR evidence. OCR is fallible.
Inspect the original drawing visually for ALL views and printed occurrences OCR missed, including small sections.
Return JSON ONLY matching this example (arrays may contain many entries):
{"name":"","unit":"mm|inch|unknown","projection":"first|third|unknown",
"projection_symbol":{"status":"present|absent|unreadable","convention":"first|third|unknown","evidence":"visible symbol description"},
"document_kind":"part|assembly|detail_sheet|mixed|unknown",
"views":[{"id":"v1","box":[0,0,1000,1000],"type":"front|plan|side|section|detail|iso|other","label":"SECTION A-A","cutting_plane":"A-A","section_evidence":"internal geometry visible","assessed":true,"questions":[]}],
"callouts":[{"id":"o1","box":[10,10,20,20],"text":"exact complete printed callout","category":"measurement|symbol|note|material|component","views":["v1"],"ocr_ids":[],"meaning":"engineering meaning","nominal":"10","unit":"mm","quantity":1,"tolerance":"exact printed tolerance","status":"matched|vision_only|ocr_only|conflict|unreadable","alternatives":[]}],
"features":[{"id":"f1","kind":"bore","description":"observed physical feature","views":["v1"],"dimensions":{"diameter":["o1"]},"questions":[]}],
"bom":[{"item":"1","part_number":"","description":"","quantity":null,"evidence":"printed row"}],
"notes":[],"questions":[],"coverage":{"dimensions":"assessed","symbols":"assessed","projection":"assessed","sections":"assessed","features":"assessed","notes":"assessed","bom":"absent"},"whole_page_checked":true}
All boxes are ORIGINAL source coordinates normalized 0..1000, not crop-sheet coordinates.
One complete view per view box; include its callouts/caption where possible. Ignore app chrome and colored annotations.
Give EVERY printed occurrence its own ID, including repeated equal values, zero references, fractions, angular and symbolic callouts.
Preserve quantities, tolerances, limits, threads, GD&T/datums, finish/welding symbols, notes and units. Unknown nominal/quantity=null.
Do not invent dimensions, derive lengths from pixels, infer projection from country/layout or treat hatching as ribs/grooves.
Link sections/cutting planes, projection, holes/cavities/wall thickness to supporting views and callouts. Retain unsupported features.
Cross-check OCR against source pixels: matched only with supporting OCR IDs; keep OCR errors and visual-only findings distinct.
Report missing/unreadable evidence and conflicting attachments as questions. Coverage is assessed/absent/unreadable/not_checked;
absent means visibly absent, not skipped. A valid JSON response is not proof of complete reading. No extra keys.
'''

VERIFY = '''Verify the supplied candidate against the ORIGINAL drawing overview and numbered source-view crops.
Find omitted views/callouts, incorrect symbols, tolerances/quantities and wrong feature associations.
Return a FULL revised extraction in the same JSON format. Preserve correct existing observations and their IDs.
New source occurrences need new unique IDs. Retain competing readings in alternatives and questions; do not guess.
Coordinates remain normalized to the original source, NOT the packed sheet. Do not remove evidence outside this sheet's crops.
Reinspect the page, symbols and section relationships. A second pass is not automatically a correct result.
'''

LEAN_PROMPT = '''Transcribe the ORIGINAL engineering drawing and visually cross-check the fallible OCR.
This is extraction, not CAD planning. Do not calculate or invent geometry. Return JSON only:
{"name":"","unit":"mm|inch|unknown","projection":"first|third|unknown","document_kind":"part|assembly|detail_sheet|mixed|unknown",
"projection_symbol":{"status":"present|absent|unreadable","convention":"first|third|unknown","evidence":""},
"views":[{"id":"v1","box":[left,top,right,bottom],"type":"plan|front|side|section|detail|iso|other","assessed":true}],
"callouts":[{"id":"o1","text":"complete printed notation","box":[left,top,right,bottom],"category":"measurement|symbol|note|material|component","views":["v1"],"ocr_ids":[],"status":"matched|vision_only|ocr_only|conflict|unreadable"}],
"features":[{"id":"f1","kind":"","description":"","views":["v1"],"dimensions":{"role":["o1"]}}],
"bom":[],"notes":[],"questions":[],"coverage":{"dimensions":"assessed","symbols":"assessed","projection":"assessed","sections":"assessed","features":"assessed","notes":"assessed","bom":"absent"},"whole_page_checked":true}
Omit empty optional fields. Views may add label (string), cutting_plane (string), section_evidence (string), questions (string array).
Callouts may add meaning, nominal (string), unit, quantity (printed integer), tolerance, alternatives.
Features may add questions. BOM rows: item,part_number,description,quantity(integer or null),evidence.
Boxes: original source normalized 0..1000. One complete view per box, including narrow sections, captions and dimensions.
Preserve EVERY printed occurrence and all digits, symbols, tolerances, quantities, zero references, fractions, threads, GD&T, datums, finish/welds.
Inspect all source pixels for OCR omissions/wrong readings, especially rotated diameter/radius/degree callouts. matched requires supporting OCR IDs.
Include all visible physical features and their source dimension links; section/cutting-plane evidence establishes internal geometry and depths.
Hatching is cut material, not ribs/grooves. Inspect actual projection symbol/title block; do not infer units or projection from layout.
Retain local units in notes without assuming global units. Ignore app chrome and colored annotations.
Concise questions must flag uncertain readings, conflicting attachments and missing sizes/positions/depths needed to build shown features.
Coverage: assessed/absent/unreadable/not_checked. Sweep the whole sheet. JSON validity does not establish complete engineering evidence.'''


@dataclass
class HybridPageRead(PageRead):
    evidence: dict = field(default_factory=dict)


def extract(llm, page_no: int, png: bytes, ocr: dict, strategy: str = "r1", *, transport="verbose") -> HybridPageRead:
    if strategy not in {"r1", "r2"}:
        raise ValueError("Hybrid strategy must be r1 or r2")
    if transport not in {"verbose","anchored","lean","split"} or strategy=="r2" and transport!="verbose":
        raise ValueError("Choose a supported response transport; R2 retains the verbose baseline")
    started = time.monotonic()
    compact_ocr = {k: ocr.get(k) for k in ("engine", "width", "height", "coordinate_frame", "angle")}
    compact_ocr["lines"] = [{k: line[k] for k in ("id", "text", "box")} for line in ocr["lines"]]
    context = compact.context(ocr) if transport=="anchored" else json.dumps({"ocr": compact_ocr}, ensure_ascii=False)
    replies = []

    def call(system, parts):
        response = llm.generate_json(system, parts)
        try:
            decoded = compact.expand(response.data,ocr) if transport=="anchored" else response.data
            parsed = Extraction.model_validate(decoded)
            known = {line["id"] for line in ocr["lines"]}
            if any(not set(c.ocr_ids) <= known for c in parsed.callouts):
                raise ValueError("Agent cited unknown OCR evidence")
        except ValueError:
            getattr(llm, "discard", lambda *a: None)(system, parts)
            raise
        replies.append({"data": parsed.model_dump(), "wire_format":compact.VERSION if compact.is_anchored(response.data) else transport,
                        "seconds": response.seconds, "usage": response.usage})
        return parsed

    source_feature_survey=None
    if transport=="split":
        from app.perception.split import SURVEY_PROMPT, Survey, bind_features
        from app.parallel import ordered_map
        readings_prompt=LEAN_PROMPT+"\n"+RUBRIC_PROMPT+"\nThis call handles source READINGS only. A parallel call handles features. Return features:[] and coverage.features:not_checked. Complete every other category. Do not analyze missing feature geometry."
        def lane(kind):
            if kind=="readings": return call(readings_prompt,[FilePart(png,"image/png"),context])
            parts=[FilePart(png,"image/png"),context]
            response=llm.generate_json(SURVEY_PROMPT,parts)
            try: survey=Survey.model_validate(response.data)
            except ValueError:
                getattr(llm,"discard",lambda *a:None)(SURVEY_PROMPT,parts)
                raise
            replies.append({"data":survey.model_dump(),"wire_format":"split-features","seconds":response.seconds,"usage":response.usage})
            return survey
        parsed,survey=ordered_map(lane,["readings","features"])
        source_feature_survey=survey.model_dump()
        parsed=bind_features(parsed,survey)
    else:
        system={"verbose":PROMPT,"anchored":compact.PROMPT,"lean":LEAN_PROMPT}[transport]
        reference_note="\nconfidence_support_views uses numeric 1-based view rows in this transport." if transport=="anchored" else ""
        parsed = call(system+"\n"+RUBRIC_PROMPT+reference_note, [FilePart(png, "image/png"), context])
    with Image.open(io.BytesIO(png)) as source:
        width, height = source.size
    pixels = lambda box: [round(v*(width if i%2==0 else height)/1000) for i,v in enumerate(box)]
    if strategy == "r2":
        original_views = list(parsed.views)
        boxes = [pixels(v.box) for v in parsed.views]
        sheets = pack(png, boxes, overview=mark_boxes(png, boxes)) if boxes else [(png, [])]
        for sheet, numbers in sheets:
            legend = {str(i): original_views[i-1].model_dump() for i in numbers}
            candidate = parsed
            parsed = call(PROMPT + VERIFY+"\n"+RUBRIC_PROMPT, [FilePart(sheet, "image/png"), context,
                         json.dumps({"candidate": parsed.model_dump(), "source_view_legend": legend}, ensure_ascii=False)])
            parsed.questions = list(dict.fromkeys(candidate.questions + parsed.questions))
            # Verification cannot silently discard the candidate's evidence.
            for attribute in ("views", "callouts", "features"):
                present = {item.id for item in getattr(parsed, attribute)}
                for item in getattr(candidate, attribute):
                    if item.id not in present:
                        getattr(parsed, attribute).append(item)
                        parsed.questions.append(f"Verification omitted {attribute} {item.id}; source review required")
                if attribute in {"views", "features"}:
                    previous = {item.id:item for item in getattr(candidate, attribute)}
                    for item in getattr(parsed, attribute):
                        if item.id in previous:
                            item.questions = list(dict.fromkeys(previous[item.id].questions + item.questions))
            parsed.validate_references()
    questions = list(parsed.questions)
    if not parsed.whole_page_checked or not parsed.views:
        questions.append("Whole-sheet/view coverage is incomplete")
    for category in sorted(CATEGORIES):
        if parsed.coverage.get(category) not in {"assessed", "absent"}:
            questions.append(f"Coverage needs review: {category}")
    for v in parsed.views:
        questions.extend(f"{v.id}: {q}" for q in v.questions)
        if not v.assessed:
            questions.append(f"View {v.id} has not been assessed")
    for index, v in enumerate(parsed.views):
        for other in parsed.views[:index]:
            a,b=v.box,other.box
            intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
            if intersection/min((a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1])) > .25:
                questions.append(f"View {v.id} overlaps {other.id}; verify crop separation and shared callouts")
    for c in parsed.callouts:
        if c.status == "matched" and not c.ocr_ids:
            c.status = "vision_only"
            questions.append(f"{c.id}: claimed OCR agreement has no OCR evidence")
        elif c.status == "matched":
            normalize = lambda value: "".join(value.casefold().replace("⌀","ø").split())
            supporting = " ".join(line["text"] for line in ocr["lines"] if line["id"] in c.ocr_ids)
            if normalize(c.text) not in normalize(supporting):
                c.status = "conflict"
                questions.append(f"{c.id}: OCR text does not support the complete callout")
        if c.status in {"conflict", "unreadable", "ocr_only"}:
            questions.append(f"{c.id}: {c.status}: {c.text}")
    for f in parsed.features:
        questions.extend(f"{f.id}: {q}" for q in f.questions)
    questions = list(dict.fromkeys(questions))
    view_ids = {v.id: i for i,v in enumerate(parsed.views, 1)}
    numeric = [c for c in parsed.callouts if c.category == "measurement"]
    dim_ids = {c.id: f"P{page_no}.d{i}" for i,c in enumerate(numeric, 1)}
    dims = []
    for c in numeric:
        tokens = dim_tokens(c.text)
        grade=rubric_score(c.confidence_metrics)
        dims.append(Dimension(dim_ids[c.id], c.text, [view_ids[v] for v in c.views], c.status != "matched",
                              tokens[0] if len(tokens)==1 else "",grade if grade is not None else c.confidence,c.confidence_reason,
                              list(c.confidence_metrics) if c.confidence_metrics is not None else None))
    views = [CropView(view_ids[v.id], pixels(v.box), v.type, v.label, dims=[c.text for c in numeric if v.id in c.views],
                      section={"cutting_plane":v.cutting_plane,"evidence":v.section_evidence,"questions":v.questions} if v.type=="section" else {})
             for v in parsed.views]
    features = [{"id":f"P{page_no}.f{i}","kind": f.kind if f.kind in {"plate","hole","boss"} else "unsupported",
                 "description":f"{f.kind}: {f.description}","views":[f"P{page_no}.v{view_ids[v]}" for v in f.views],
                 "dimensions":{role:[dim_ids[d] for d in ids if d in dim_ids] for role,ids in f.dimensions.items()}}
                for i,f in enumerate(parsed.features,1)]
    payload = {"contract_version":"evidence/v1","strategy":strategy,"page":page_no,"ocr":ocr,
               "extraction":parsed.model_dump(),"attempts":replies,"issues":questions,
               "processing":"finished","coverage_status":"needs_review" if questions else "agent_assessed",
               "human_review":"pending","cad_ready":False,"features":{"features":features,"questions":questions},
               "model_seconds":round(time.monotonic()-started,3),"model":llm.model,
               "settings":getattr(llm,"cache_settings",{})}
    if source_feature_survey is not None: payload["source_feature_survey"]=source_feature_survey
    trace.current().event("hybrid", "extraction finished", strategy=strategy, calls=len(replies), views=len(views),
                          callouts=len(parsed.callouts), issues=len(questions))
    tb = {"name":parsed.name,"unit":parsed.unit,"projection":parsed.projection,
          "projection_symbol":parsed.projection_symbol.model_dump(),"document_kind":parsed.document_kind}
    return HybridPageRead(page_no,dims,views,tb,parsed.notes,len(replies),[c.text for c in numeric],questions,payload)
