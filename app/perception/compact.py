"""OCR-anchored transport expanded into the unchanged strict evidence contract."""
import json

VERSION = "ocr-anchors-v2"
PROMPT = '''Visually inspect this ORIGINAL mechanical drawing and cross-check the fallible positioned OCR.
This is source extraction only. Do not plan CAD operations, solve geometry or derive unprinted sizes.
State uncertain or missing evidence as concise questions instead of trying to reconstruct hidden geometry.
Return JSON only, with format="ocr-anchors-v2" and these fields:
name, unit (mm/inch/unknown), projection (first/third/unknown), document_kind (part/assembly/detail_sheet/mixed/unknown),
projection_symbol:{status:present/absent/unreadable,convention:first/third/unknown,evidence:string},
views:[{box:[left,top,right,bottom],type:plan/front/side/section/detail/iso/other,assessed:true,label,cutting_plane,section_evidence,questions:[]}],
callouts:[{text,box,views:[view_numbers],ocr_rows:[OCR_numbers],status:matched/vision_only/ocr_only/conflict/unreadable,
category:measurement/symbol/note/material/component,meaning,nominal:string_or_null,unit,quantity:integer_or_null,tolerance,alternatives:[]}],
features:[{kind,description,views:[view_numbers],dimensions:{role:[callout_numbers]},questions:[]}],
bom:[{item,part_number,description,quantity:integer_or_null,evidence}],notes:[],questions:[],
coverage:{dimensions,symbols,projection,sections,features,notes,bom},whole_page_checked:true.
Coverage values: assessed/absent/unreadable/not_checked. All row references are 1-based array positions; omit IDs.
All boxes use original page coordinates normalized 0..1000. One complete view per box, including narrow sections, captions and dimensions.
SHORTCUT: after visually confirming a whole OCR line is ONE complete correct callout, write {ocr_row:N,views:[...]}.
This inherits its exact text and box, status matched and category measurement. Only use this when OCR frame is source.
Otherwise provide text and box; ocr_rows optionally cite fallible supporting lines. Omit empty optional fields and unknown nominal/quantity.
Do not copy all OCR blindly. Inspect all pixels for missing/rotated numbers, symbols, views and notes. Preserve EVERY printed occurrence,
complete quantities/tolerances/threads/GD&T/datums/finish/welds, zero references, fractions, and diameter versus radius versus degrees.
Retain all features and cite dimensions and sections establishing their sizes, positions and depths. Hatching is cut material, not grooves.
Inspect projection symbol/title block. Never infer projection, units, equality, symmetry or dimensions from layout or pixels.
Missing feature locations/depths, unclear readings/attachments and unsupported geometry require precise questions; never guess.
Preserve local units in notes without assuming global units. Ignore app chrome/colored boxes. Do a whole-page coverage sweep.
Keep descriptions concise; do not explain ordinary numeric callouts. No extra fields or markdown.'''


def context(ocr):
    width,height=ocr["width"],ocr["height"]
    lines=[[line["text"],[round(v*1000/(width if i%2==0 else height)) for i,v in enumerate(line["box"])]]
           for line in ocr["lines"]]
    return json.dumps({"ocr_frame":ocr.get("coordinate_frame"),"ocr_angle":ocr.get("angle"),
                       "ocr_rows_1_based":lines},ensure_ascii=False,separators=(",",":"))


def _quantity(value):
    # Normalize an integer serialized as text, never infer a missing quantity.
    if isinstance(value,str) and value.isascii() and value.isdecimal():
        return int(value)
    return value


def expand(data, ocr):
    anchored=is_anchored(data)
    if isinstance(data,dict) and "format" not in data and not anchored:
        return data  # Verbose replies still pass through strict schema validation.
    if not isinstance(data,dict) or data.get("format",VERSION)!=VERSION:
        raise ValueError("Unknown extraction transport")
    result={k:v for k,v in data.items() if k!="format"}
    for name in ("views","callouts","features"):
        if not isinstance(result.get(name),list) or any(not isinstance(row,dict) for row in result[name]):
            raise ValueError("Evidence collections must contain objects")
    def refs(values,kind):
        if not isinstance(values,list): raise ValueError("Source references must be arrays")
        count={"v":len(result["views"]),"o":len(result["callouts"]),"ocr":len(ocr["lines"])}[kind]
        if any(type(n) is not int or not 1<=n<=count for n in values):
            raise ValueError("Unknown source reference")
        return [ocr["lines"][n-1]["id"] if kind=="ocr" else f"{kind}{n}" for n in values]
    if any("id" in row for row in result["views"]): raise ValueError("Use transport row references")
    result["views"]=[{**row,"id":f"v{i}"} for i,row in enumerate(result["views"],1)]
    callouts=[]
    for i,row in enumerate(result["callouts"],1):
        row=dict(row)
        if "id" in row or "ocr_ids" in row: raise ValueError("Use transport row references")
        if "ocr_row" in row:
            index=row.pop("ocr_row")
            ids=refs([index],"ocr")
            if ocr.get("coordinate_frame")!="source":
                raise ValueError("Rotated OCR boxes cannot anchor source observations")
            if {"text","box","ocr_rows","status"} & row.keys():
                raise ValueError("OCR anchors cannot override inherited source fields")
            source=ocr["lines"][index-1]
            row.update(text=source["text"],box=[round(v*1000/(ocr["width"] if j%2==0 else ocr["height"]))
                                               for j,v in enumerate(source["box"])],ocr_ids=ids,status="matched")
        else:
            row["ocr_ids"]=refs(row.pop("ocr_rows",[]),"ocr")
        row.update(id=f"o{i}",views=refs(row.get("views",[]),"v"))
        if "confidence_support_views" in row:
            row["confidence_support_views"]=refs(row["confidence_support_views"],"v")
        row.setdefault("category","measurement")
        if "quantity" in row: row["quantity"]=_quantity(row["quantity"])
        callouts.append(row)
    result["callouts"]=callouts
    features=[]
    for i,row in enumerate(result["features"],1):
        if "id" in row: raise ValueError("Use transport row references")
        dimensions=row.get("dimensions",{})
        if not isinstance(dimensions,dict): raise ValueError("Feature dimensions must be an object")
        features.append({**row,"id":f"f{i}","views":refs(row.get("views",[]),"v"),
                         "dimensions":{role:refs(indices,"o") for role,indices in dimensions.items()}})
    result["features"]=features
    if "bom" in result:
        if not isinstance(result["bom"],list) or any(not isinstance(row,dict) for row in result["bom"]):
            raise ValueError("BOM rows must be objects")
        result["bom"]=[{**row,"quantity":_quantity(row.get("quantity"))} for row in result["bom"]]
    return result


def is_anchored(data):
    if not isinstance(data,dict): return False
    if data.get("format")==VERSION: return True
    # Some providers omit the format marker. Recognize the explicit transport
    # fields, never infer a measurement, coordinate or source reference.
    rows=data.get("callouts",[])
    return (isinstance(rows,list) and any(isinstance(row,dict) and ("ocr_row" in row or "ocr_rows" in row)
                                        for row in rows)) or (
        isinstance(data.get("views"),list) and bool(data["views"]) and
        all(isinstance(row,dict) and "box" in row and "id" not in row for row in data["views"]))
