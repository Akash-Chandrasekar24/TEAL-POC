"""Whole-sheet BOM extraction; repeated tables are evidence, not extra parts."""
from collections import defaultdict

from pydantic import Field

from app.drawing.model import Strict
from app.llm.base import FilePart
from .model import AssemblyPlan, BOMRow, Evidence, digest


class PrintedRow(Strict):
    item: str = Field(min_length=1)
    part_number: str = ""
    description: str = ""
    quantity: int | None = Field(default=None, ge=1, strict=True)
    kind: str = "unknown"
    scope: str = ""
    text: str = Field(min_length=1)


class PageBOM(Strict):
    rows: list[PrintedRow]
    unit: str | None = None
    issues: list[str] = Field(default_factory=list)


PROMPT = """Read the bill of materials from this engineering drawing PAGE, including its tables.
Return JSON {"rows":[{"item":"printed item/balloon number", "part_number":"printed exact identifier or empty",
"description":"printed description", "quantity":integer or null, "kind":"part|assembly|unknown",
"scope":"parent assembly identifier for a nested BOM, empty for root BOM", "text":"verbatim row"}],
"unit":"mm|inch or null", "issues":["unreadable, incomplete or ambiguous table details"]}.
Use only visible text. No invented parts, quantities, dimensions or placements. Do not infer kind
from appearance: assembly if explicitly labeled assembly, part if explicitly identified as a single
part, otherwise unknown. Do not turn the title block's drawing number into a BOM row.
If there is no BOM return no rows and explain whether a continuation sheet might contain it.
"""


def read_bom(llm, result: dict, pictures: dict[int, bytes]) -> AssemblyPlan:
    grouped = defaultdict(list)
    units, issues = set(), []
    from app.parallel import ordered_map
    def read_one(page):
        no = page["no"]
        if no not in pictures:
            return None
        return PageBOM.model_validate(llm.generate_json(PROMPT, [f"Page {no}", FilePart(pictures[no], "image/png")]).data)
    replies = ordered_map(read_one, result["pages"])
    for page, reply in zip(result["pages"], replies):
        no = page["no"]
        if reply is None:
            issues.append(f"Page {no}: source image is missing; restore it and reread the BOM")
            continue
        if reply.unit:
            units.add(reply.unit)
        issues.extend(f"Page {no}: {issue}" for issue in reply.issues)
        for row in reply.rows:
            grouped[(row.scope.strip(), row.item.strip())].append((no, row))
    rows = []
    for i, ((scope, item), readings) in enumerate(grouped.items(), 1):
        first = readings[0][1]
        conflicts = []
        for field in ("part_number", "quantity", "description", "kind"):
            if len({getattr(r, field) for _, r in readings}) > 1:
                conflicts.append(f"Conflicting {field}: " + "; ".join(f"page {p}: {getattr(r, field)}" for p, r in readings))
        rows.append(BOMRow(id=f"B{i}", part_number=first.part_number, description=first.description,
                           quantity=first.quantity, kind=first.kind if first.kind in ("part", "assembly") else "unknown",
                           scope=scope, evidence=[Evidence(page=p, text=f"item {item}: {r.text}") for p, r in readings], issues=conflicts))
    if len(units) != 1 or next(iter(units)) not in ("mm", "inch"):
        issues.append("Confirm length units across all sheets; missing or conflicting unit readings")
    return AssemblyPlan(source_hash=digest(result), unit=next(iter(units)) if len(units) == 1 and units <= {"mm", "inch"} else None,
                        bom=rows, issues=issues)
