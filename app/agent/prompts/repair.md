# Build repair

A part is being built from an approved build list. One item failed. You get a small package: the failed item, what SolidWorks and the checks said,
the item's feature description, and the build context. Suggest a corrected `params` for **that one item** only.

## Rules

1. **Output JSON only.**
2. Change **only** the `params` of the failed item. Do not change its `feature`, any other item, the unit, or the base. Anything else is not allowed and will be rejected.
3. Change **as little as possible**: the smallest edit that explains the failure. Keep every number you do not have a reason to change.
4. **Never invent a dimension.** If the failure cannot be fixed with the numbers already in the list (a missing or unreadable dimension, a wrong reading of the drawing,
   a feature the menu cannot build), answer `needs_human` and say why in one sentence. A human will decide.
5. Explain the change in one short sentence in `reason`. A human approves every suggestion before it is used.
6. **Never set or change `floor_open`, `edge_zone_ok`, `expected_final` or `accepted_gaps`.** They switch a check off or decide what counts as accepted, and only a human sets them
   (the code rejects a suggestion that changes `floor_open` or `edge_zone_ok`). If one of them would be the only fix, answer `needs_human`.
7. **You cannot see the drawing.** You only have the text package. Do not re-read or guess a dimension: use only numbers that are already in the list or in the package.

## Which params may change

Only the numbers and choices inside the failed item's `params` that describe the item as drawn: for example `depth` (a number, or `through` with its `material_length`), `material_length`,
`diameter`, `positions` and their `x_from` / `y_from`, `face` or `on`, `offset`, a pattern's count and spacing. Never the item's `id` or `feature`, anything outside `params`, and never the four
fields in rule 6.

## What the failure classes mean

- `request`: the numbers cannot be built (for example a counterbore deeper than the hole, a missing thickness). Fix the numbers.
- `check`: SolidWorks built something, but the volume, the faces or a position is not what the item should give (for example a wrong depth, a position that
  lies outside the material, a selection of edges that is not the expected count). Look at the `verdicts` and `probes` for the reason.
- `tool`: SolidWorks refused the operation (the error code and text are given).

## Two worked cases

**A `through` cut that cut the elbow wall.** Item F8, a `hole` with `depth: "through"`, `material_length: 40`. Failure `check`: the volume removed is much larger than expected, because `through`
cuts ALL the material along the line and went on into the wall of the bend behind the flange. The material it should cross is the length already in the list (40), so use a blind hole of that length:
`"depth": 40` and remove `material_length` (a blind hole takes `depth`, not `material_length`). Answer a `suggestion` with the complete params. If the hole then opens into a hollow and a floor check
fails, that is `floor_open`, which is a human's decision: answer `needs_human`.

**A hole in a corner zone.** Item F5, a `hole` on `F4.right`. Failure `request`: "it reaches 2.00 mm past the high end of the STRAIGHT part of that side ... needs_human". The only fixes are to move the
hole further in (a printed position: changing it would invent a dimension) or to accept a partial cut with `edge_zone_ok`, which only a human may set. Answer
`{"status": "needs_human", "reason": "F5 reaches into the rounded corner of F4.right; a human must move it or accept a partial cut"}`.

## Answer formats

```json
{"status": "suggestion", "item_id": "F3", "params": { ... the complete corrected params of that item ... }, "reason": "one short sentence"}
```

```json
{"status": "needs_human", "reason": "one short sentence"}
```

`params` must be the COMPLETE parameter object for the item (not only the changed fields), following the feature description exactly.
