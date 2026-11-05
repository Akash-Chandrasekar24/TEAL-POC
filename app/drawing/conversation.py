"""Bounded conversation over the current drawing's extracted evidence.

Answers are assembled from allowlisted source fields and a small notation
glossary. Neither user questions nor source text can execute instructions,
access another drawing or manufacture a missing dimension.
"""
import re
from .confidence import review_index,CRITERIA

REFUSAL="I can help only with this drawing's extracted measurements, views, sections, symbols and review questions. Select a reading or ask about the drawing."
OUTSIDE=re.compile(r"ignore.{0,35}(?:instruction|prompt|rule)|system\s*prompt|developer\s*(?:message|instruction)|(?:another|different|previous)\s+(?:drawing|session)|\b(?:weather|recipe|politic\w*|stock|crypto|football|movie|joke|password|secret|python|javascript|powershell|sql|malware|email)\b|\b(?:write|generate|execute|run)\s+(?:code|script|command)",re.I)
GLOSSARY={"diameter":"Ø denotes a diameter; it is not a radius.","radius":"R denotes a radius; it is not a diameter.",
          "tolerance":"A printed ± tolerance gives the permitted deviation around the stated nominal value. I do not infer an unprinted tolerance.",
          "angle":"° denotes an angular dimension, not a linear length.",
          "confidence":"Confidence is calculated from five extraction-agent ratings (0–5), not OCR scores or a calibrated accuracy probability. Weights: readability 30%, attachment 25%, view/section consistency 20%, completeness 15%, mechanical interpretation 10%. Above 80% is eligible for AI approval only when all critical criteria and source checks pass."}


def answer_question(result,model,question,target="note"):
    if not isinstance(question,str) or not question.strip() or len(question)>4000:
        raise ValueError("Ask a drawing question of up to 4000 characters")
    question=question.strip();query=question.casefold()
    if OUTSIDE.search(question): return {"answer":REFUSAL,"citations":[],"scope":"refused"}
    reviews=review_index(result,model)
    by_id={d.id:d for d in model.dimensions}
    ids=["P"+match[1:].lower() for match in re.findall(r"P\d+\.[dh]\d+",question,re.I)]
    if target in by_id and not ids: ids=[target]
    if ids and any(identity not in by_id for identity in ids):
        return {"answer":"That measurement ID is not in this drawing. Choose one of the current extracted readings.","citations":[],"scope":"clarification"}
    lines=[];citations=[]
    def cite(identity):
        if identity not in citations: citations.append(identity)
    questions={f"P{p['no']}.u{i}":issue for p in result["pages"] for i,issue in enumerate((p.get("read") or {}).get("issues",[]),1)}
    if target in questions:
        cite(target);lines.append("Recorded extraction question: "+questions[target])
        resolved=next((c for c in model.resolutions.clarifications if c.id==target),None)
        lines.append("Your saved clarification: "+resolved.answer if resolved else "This source question is unresolved. Check the indicated drawing detail and use Correct a reading to save the answer; I will not invent the missing value.")
    if not ids:
        numbers=set(re.findall(r"(?<![\w.])\d+(?:\.\d+)?",question))
        ids=[d.id for d in model.dimensions if numbers & set(re.findall(r"\d+(?:\.\d+)?",d.effective_text))]
    for identity in dict.fromkeys(ids[:8]):
        d=by_id[identity];review=reviews[identity];cite(identity)
        source=f"page {d.page}, {', '.join(d.views) or 'source view not located'}"
        state="verified by you" if d.confirmed else "AI approved" if review["ai_approved"] else "needs review"
        score="not scored by the extraction agent" if review["confidence"] is None else f"agent confidence {review['confidence']:g}%"
        lines.append(f"{identity}: {d.effective_text} ({source}). {score}; {state}.")
        if d.corrected_text is not None: lines.append(f"Original extraction: {d.text}. The confidence refers to that original reading; the current value is your saved correction.")
        if review["confidence_reason"]: lines.append("Agent's reading rationale: "+review["confidence_reason"])
        if "confidence" in query or "score" in query or "criteria" in query:
            for criterion in review.get("confidence_metrics",[]):
                lines.append(f"{criterion['label']}: {criterion['rating']}/5; weight {criterion['weight']}%.")
        if review["needs_review"]: lines.append(review["review_reason"])
        page=next((p for p in result["pages"] if p["no"]==d.page),None)
        if page:
            read=page.get("read") or {};evidence=read.get("evidence",{}).get("extraction",{})
            index=next((i for i,raw in enumerate(read.get("dims",[])) if raw["id"]==identity),None)
            callouts=[c for c in evidence.get("callouts",[]) if c.get("category")=="measurement"]
            if index is not None and index<len(callouts) and callouts[index].get("text")==d.text:
                callout=callouts[index]
                if callout.get("meaning"): lines.append("Recorded interpretation: "+callout["meaning"])
                for feature in evidence.get("features",[]):
                    roles=[role for role,refs in feature.get("dimensions",{}).items() if callout["id"] in refs]
                    if roles:
                        lines.append(f"Feature {feature['id']}: {feature.get('description','')}. Linked role: {', '.join(roles)}.")
                        cite(f"P{d.page}.{feature['id']}")
    for key,definition in GLOSSARY.items():
        if key in query or key=="confidence" and ("criteria" in query or "score" in query) or key=="diameter" and "ø" in query or key=="tolerance" and "±" in query:
            lines.append(definition)
    if re.search(r"\b(?:unit|millimet|millimet|inch)\w*\b",query):
        lines.append("The reviewed drawing unit is "+({"mm":"millimetres","inch":"inches"}.get(model.unit,"unknown; confirm it from the source"))+".")
        citations.extend(f"P{p.no}.metadata" for p in model.pages)
    if "projection" in query or "third angle" in query or "first angle" in query:
        lines.append("The reviewed projection is "+({"first":"first angle","third":"third angle"}.get(model.projection,"unknown; no convention will be inferred"))+".")
        for page in model.pages:
            symbol=page.title_block.get("projection_symbol")
            if isinstance(symbol,dict):
                lines.append(f"Page {page.no} extracted projection symbol: {symbol.get('status','unreadable')}. {symbol.get('evidence','')}")
            cite(f"P{page.no}.metadata")
    if re.search(r"section|cutting|hatch|\b[abc]-[abc]\b",query):
        sections=[(p,v) for p in model.pages for v in p.views if v.type=="section" or v.section]
        for page,view in sections:
            lines.append(f"{view.id}: {view.section.get('cutting_plane') or view.label or 'section reference not read'}. {view.section.get('evidence','No internal-geometry evidence was returned.')}")
            for issue in view.section.get("questions",[]): lines.append("Open section question: "+issue)
            cite(view.id)
        if not sections: lines.append("No section was recorded in this extraction. That does not prove the source has no section; select the view that needs checking.")
    if re.search(r"\b(?:feature|hole|bore|boss|pocket|cavity|slot|rib)\w*\b",query):
        observations=(result.get("design") or {}).get("plan",{}).get("interpretations") or result.get("extracted_features") or []
        features=[f for page in observations for f in page.get("features",[])]
        terms=set(re.findall(r"\w+",query))&{"hole","bore","boss","pocket","cavity","slot","rib"}
        matches=[f for f in features if not terms or terms&set(re.findall(r"\w+",(f.get("kind","")+" "+f.get("description","")).casefold()))]
        for feature in matches[:6]:
            lines.append(f"{feature['id']}: {feature.get('description') or feature.get('kind','Observed feature')}.")
            cite(feature["id"])
        if not matches: lines.append("No matching physical-feature record is available yet. I will not infer its size or location from the image scale.")
    if re.search(r"missing|unclear|conflict|question|doubt|why|unreadable",query):
        issues=list(dict.fromkeys(model.issues+(result.get("design") or {}).get("plan",{}).get("questions",[])))
        if ids: issues=[issue for issue in issues if any(identity in issue for identity in ids)]
        lines.extend("Open review question: "+issue for issue in issues[:6])
        if "why" in query and ids: lines.append("I can report the source reading and recorded uncertainty. I cannot invent the designer's intent or an unprinted value.")
    if not lines and re.search(r"drawing|measurement|dimension|reading|view|extract|\bhello\b|\bhi\b",query):
        lines.append(f"This drawing has {len(model.pages)} page(s) and {len(model.dimensions)} measurement readings. Ask about a reading ID, its confidence, a section, feature, unit or projection.")
        for d in model.dimensions[:8]: lines.append(f"{d.id}: {d.effective_text} · page {d.page}");cite(d.id)
    if not lines: return {"answer":REFUSAL,"citations":[],"scope":"refused"}
    return {"answer":"\n\n".join(lines),"citations":list(dict.fromkeys(citations)),"scope":"drawing_evidence"}
