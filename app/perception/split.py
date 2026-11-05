"""Parallel source-feature survey; all bindings must resolve to positioned readings."""
from pydantic import Field, field_validator
from app.perception.hybrid import Strict, Feature, Extraction

SURVEY_PROMPT='''Inspect this ORIGINAL mechanical drawing for ALL visible physical features and section relationships.
A parallel source reader transcribes all measurements, views, notes, symbols and projection. Do not repeat that inventory.
Your task is a concise mechanical feature survey, NOT CAD planning or solving hidden geometry.
Return JSON only:
{"features":[{"kind":"bore","description":"observed feature","at":[[x,y]],
"dimensions":{"diameter":[{"text":"exact complete printed callout","at":[x,y]}]},"questions":[]}],
"questions":[],"whole_page_checked":true}
All positions use original-page coordinates normalized 0..1000. Each feature at point is INSIDE the physical geometry
in one supporting orthographic/section/detail view; add multiple points for supporting views when necessary.
Each dimension at point is INSIDE its printed callout text, not the geometry or leader arrow.
Preserve all digits, quantities, tolerances, radius/diameter/angle symbols, fractions and threads. OCR is fallible; inspect source pixels.
Retain all observed features even when incomplete or unsupported. Include section-dependent internal holes/cavities/walls/depths.
Hatching means cut material, not grooves/ribs. Never infer dimensions, equality, symmetry or hidden geometry from pixels/layout.
Use dimensions:{} where no size is printed. Concise questions identify missing sizes, locations, depths and unclear attachments.
Ignore app chrome and colored boxes. Sweep every view, including narrow sections. Do not ask for optional drawing metadata.'''


class ReadingRef(Strict):
    text:str=Field(min_length=1)
    at:tuple[float,float]

    @field_validator("at")
    @classmethod
    def valid_point(cls,point):
        if any(not 0<=v<=1000 for v in point): raise ValueError("Source point is outside the page")
        return point


class SourceFeature(Strict):
    kind:str=Field(min_length=1)
    description:str
    at:list[tuple[float,float]]=Field(min_length=1)
    dimensions:dict[str,list[ReadingRef]]=Field(default_factory=dict)
    questions:list[str]=Field(default_factory=list)

    @field_validator("at")
    @classmethod
    def valid_points(cls,points):
        if any(not 0<=v<=1000 for point in points for v in point): raise ValueError("Feature point is outside the page")
        return points


class Survey(Strict):
    features:list[SourceFeature]
    questions:list[str]=Field(default_factory=list)
    whole_page_checked:bool=False


def bind_features(readings:Extraction,survey:Survey):
    # Preserve any unexpected candidate features rather than silently dropping them.
    output=readings.model_copy(deep=True)
    normalize=lambda text:"".join(text.casefold().replace("⌀","ø").split())
    contains=lambda box,point:box[0]<=point[0]<=box[2] and box[1]<=point[1]<=box[3]
    output.questions.extend(survey.questions)
    for index,source in enumerate(survey.features,1):
        questions=list(source.questions);views=[];dimensions={}
        for point in source.at:
            matches=[v.id for v in output.views if contains(v.box,point)]
            if len(matches)!=1:
                questions.append(f"Feature view binding needs review at {point}: {len(matches)} candidate views")
            views.extend(matches)
        for role,refs in source.dimensions.items():
            dimensions[role]=[]
            for ref in refs:
                matches=[c.id for c in output.callouts if normalize(c.text)==normalize(ref.text) and contains(c.box,ref.at)]
                if len(matches)==1: dimensions[role].append(matches[0])
                else: questions.append(f"{role}: source reading {ref.text!r} at {ref.at} has {len(matches)} matches; confirm attachment")
        existing={f.id for f in output.features};identity=f"survey{index}"
        while identity in existing: identity="_"+identity
        output.features.append(Feature(id=identity,kind=source.kind,description=source.description,
                                       views=list(dict.fromkeys(views)),dimensions=dimensions,questions=questions))
    output.whole_page_checked=output.whole_page_checked and survey.whole_page_checked
    output.coverage["features"]="assessed" if survey.whole_page_checked and survey.features else "not_checked"
    return output.validate_references()
