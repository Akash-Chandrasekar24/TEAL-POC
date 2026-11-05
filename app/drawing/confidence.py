"""Extraction-agent estimates, with conservative and reproducible review routing.

Scores are model-reported reading confidence, never OCR confidence or a
calibrated probability. Unknown scores stay unknown; no heuristic invents one.
"""
import math

THRESHOLD = 80
RUBRIC_VERSION = "agent-rubric-v1"
CRITERIA = (
    ("readability","Digits and symbols are readable",30),
    ("attachment","Correct view and feature attachment",25),
    ("consistency","View and section consistency",20),
    ("completeness","Complete printed callout",15),
    ("interpretation","Mechanical meaning is clear",10),
)


def rubric_spec():
    return {"version":RUBRIC_VERSION,"threshold":THRESHOLD,"comparison":"strictly_greater_than",
            "criteria":[{"key":key,"label":label,"weight":weight} for key,label,weight in CRITERIA],
            "scale":{"0":"not assessed or unreadable","1":"contradictory or guessed","2":"weak or partial",
                     "3":"plausible; single-view consistency only","4":"clear with supporting evidence","5":"fully clear and supported"},
            "calibrated_accuracy":False}


def rubric_score(metrics):
    if not isinstance(metrics,(list,tuple)) or len(metrics)!=len(CRITERIA) or any(type(v) is not int or not 0<=v<=5 for v in metrics):
        return None
    return round(sum(value*criterion[2]/5 for value,criterion in zip(metrics,CRITERIA)),1)


# Reused verbatim in each extraction prompt. No OCR metric supplies these grades.
RUBRIC_PROMPT = '''Every measurement needs confidence_metrics:[readability,attachment,consistency,completeness,interpretation],
five integer ratings 0..5, and a brief confidence_reason naming the source evidence or uncertainty. No overall-score arithmetic is needed.
Weights are 30/25/20/15/10. 0=unassessed/unreadable,1=guessed/contradictory,2=weak/partial,3=plausible,4=clear/supported,5=fully clear/supported.
Readability: every digit/symbol legible. Attachment: correct view, leaders/endpoints and physical feature.
Consistency: check applicable views/sections of THAT feature; use 3 for single-view support, 0 if not checked.
Completeness: retain ALL printed quantities, tolerances, units, fits and depth qualifiers; never invent omitted details.
Interpretation: distinguish diameter/radius/angle/thread/depth and sectioned material. Lower any uncertain rating.
Consistency 4/5 requires confidence_support_views naming at least two supporting view IDs and a rationale for their agreement.
Do not use matching values alone to infer feature identity. These are YOUR evidence-based ratings, not OCR scores or calibrated accuracy.'''


def agent_score(value):
    if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=100:
        return None
    return float(value)


def evaluate(page,dimension):
    read=page.get("read") or {}
    evidence=read.get("evidence",{}).get("extraction")
    observation=dimension
    blocker=""
    if evidence is not None:
        observations=[c for c in evidence.get("callouts",[]) if c.get("category")=="measurement"]
        index=next((i for i,d in enumerate(read.get("dims",[])) if d["id"]==dimension["id"]),None)
        if index is None or index>=len(observations) or observations[index].get("text")!=dimension["text"]:
            observation={};blocker="The confidence is not bound to this source occurrence."
        else:
            observation=observations[index]
            if observation.get("status") in {"conflict","unreadable","ocr_only"} or observation.get("alternatives"):
                blocker="The extraction agent reported conflicting or uncertain evidence."
            assessed={v["id"] for v in evidence.get("views",[]) if v.get("assessed") is True}
            if not set(observation.get("views",[]))<=assessed:
                blocker="The supporting source view still needs assessment."
    else:
        from app.perception.dims import dim_tokens
        if len(dim_tokens(dimension.get("text","")))!=1:
            blocker="The full callout needs interpretation before it can be used as one measurement."
    metrics=observation.get("confidence_metrics")
    weighted=rubric_score(metrics)
    estimate=agent_score(observation.get("confidence"))
    score=weighted if weighted is not None else estimate
    reason=observation.get("confidence_reason")
    reason=reason if isinstance(reason,str) else ""
    if weighted is None:
        blocker=blocker or "The extraction agent has not assessed the five confidence criteria."
    else:
        if any(metrics[i]<4 for i in (0,1,3,4)) or metrics[2]<3:
            blocker=blocker or "A critical confidence criterion lacks clear supporting evidence."
        if metrics[2]>=4:
            support=set(observation.get("confidence_support_views") or [])
            if evidence is None or len(support)<2 or not support<=assessed:
                blocker=blocker or "Cross-view confidence lacks two assessed supporting views."
        if not reason.strip(): blocker=blocker or "The extraction agent did not give a source-based rationale."
    if not dimension.get("views"): blocker="The reading has not been located in a source view."
    if score is None: blocker=blocker or "The extraction agent did not return a confidence score."
    elif score<=THRESHOLD: blocker=blocker or "The extraction agent's confidence is 80% or lower."
    return {"id":dimension["id"],"confidence":score,"confidence_reason":reason,
            "confidence_source":"extraction_agent" if score is not None else None,
            "confidence_method":RUBRIC_VERSION if weighted is not None else "unstructured_estimate" if estimate is not None else None,
            "agent_reported_estimate":estimate,"confidence_metrics":[{"key":key,"label":label,"weight":weight,"rating":metrics[i]} for i,(key,label,weight) in enumerate(CRITERIA)] if weighted is not None else [],
            "ai_approved":score is not None and score>THRESHOLD and not blocker,
            "review_reason":blocker,"threshold":THRESHOLD}


def review_index(result,model):
    source={d["id"]:evaluate(page,d) for page in result["pages"] for d in (page.get("read") or {}).get("dims",[])}
    decisions={d.id:d for d in model.resolutions.dimensions}
    output={}
    for dimension in model.dimensions:
        item=dict(source.get(dimension.id,{"id":dimension.id,"confidence":None,"confidence_source":None,
                         "confidence_reason":"","confidence_metrics":[],"confidence_method":None,"ai_approved":False,"review_reason":"Human source clarification.","threshold":THRESHOLD}))
        decision=decisions.get(dimension.id)
        if decision is not None and not decision.confirmed:
            item.update(ai_approved=False,review_reason="Flagged for human review.")
        if dimension.corrected_text is not None or dimension.confirmed:
            item["ai_approved"]=False
        item["human_confirmed"]=dimension.confirmed
        item["needs_review"]=not item["ai_approved"] and not dimension.confirmed
        output[dimension.id]=item
    return output
