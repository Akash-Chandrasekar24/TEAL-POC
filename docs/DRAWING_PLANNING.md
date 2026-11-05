# Drawing evidence to an approvable build list

The browser can now take a saved dimension read through review, visual feature
interpretation, cross-view planning, and offline compilation into the existing
build-list format. Generating or downloading a list does not start SolidWorks.

## Use

```powershell
.venv\Scripts\python.exe -m app.web.server
```

1. Open `http://127.0.0.1:8765`, upload drawings, and read their dimensions.
2. In **Review and plan**, confirm units and projection. Check single-source or
   unplaced dimensions against their source page. Correct a row if its digit is
   wrong; the original reading and page/crop evidence remain unchanged.
3. Add a clarification about feature identity or corresponding views if needed.
   Missing numeric values must be supplied as explicit dimension corrections,
   not invented in a planning note.
4. Click **Generate build-list draft**. Review the resulting features, parameters,
   printed sources, derivations, proposed relationships and remaining questions.
5. Download only a draft marked **ready for your approval**. Save it locally and
   start it with the existing CLI:

```powershell
.venv\Scripts\python.exe scripts\run_job.py start --list "drawing.build-list.json" --job-id reviewed_part
```

The CLI pauses for list approval before building and for review after building.
Starting from a saved list does not provide the CLI with an image for a later
re-read; edit the list or return to the browser to generate another draft.

## Initial scope

The compiler supports a single rectangular plate, individual plain circular holes,
and circular bosses. Features may use the plate's front/back faces or a preceding
boss's free end. Plate holes must precede bosses. Through holes require an explicit
material-length binding. Unknown positions, repeated-hole groups with incomplete
positions, nonrectangular bases, threads, counterbores/countersinks, slots, bends,
fillets and other excluded geometry remain questions or unsupported observations.

This is deliberately narrower than the existing CAD menu. It establishes the
evidence and planning contract before adding more compiler recipes. It does not
enable new native SolidWorks operations.

## Data and stages

- `app/drawing/model.py`: Drawing Model version 1. Keeps page files/hashes,
  title-block readings, view IDs/boxes, dimension observations and raw independent
  page/crop readings. Equal numeric values remain distinct observations. View
  attribution is value-level, not a measured callout coordinate or proof of feature
  identity. Old results lacking an unambiguous normalized component require review.
- `Resolutions`: explicit human confirmations, corrected text, metadata choices
  and a clarification note. Units are not automatically converted. The source hash
  covers the original read; the model digest additionally covers resolutions.
- `interpret`: one visual model call per page, with numbered view boxes and a
  dimension-ID ledger. Produces physical feature observations, role associations,
  and targeted questions. This step may still misinterpret the drawing.
- `plan`: one text model call to reconcile the page interpretations into a proposal.
  Explicit observation mappings distinguish several views of one feature from
  several physical features. The compiler rejects dropped or multiply assigned
  observations, changed feature kinds and unknown sources or relationships.
- `compile_plan`: deterministic arithmetic and conversion to existing menu items.
  Every length cites dimension IDs or an explained centreline/origin. Arithmetic
  supports `+ - * /`, variables, and scalar literals `0`, `1`, `2`; length powers
  are checked. No Python evaluation, tool calls or arbitrary numeric constants.
  Fractional callouts require a confirmed decimal nominal value in this version.
- The existing `validate_list` runs on the generated draft. Unresolved questions
  also appear in `needs_human`, so handing a blocked draft to the CLI cannot skip
  its validation gate. Unsupported geometry is retained rather than approximated.

Two-read agreement is not engineer confirmation. Relationships and centreline
interpretations are model proposals for human review. Numeric provenance proves
where a value came from and how it was calculated; it does not prove that the
model assigned the value to the correct physical feature. The completeness of
visual observations must also be checked against the source drawing.

## Persistence and API

- Original reads: `storage/web/<result>.json` (not overwritten by corrections).
- Immutable planning attempts: `storage/plans/<result>/<attempt>.json`.
- Latest saved review/proposal: `storage/plans/<result>/latest.json`, replaced
  atomically. Reopening the drawing result restores it.
- Planning traces: `storage/runs/<attempt>/`; provider failures expose a trace link.
- `GET /api/result/<result>` returns the read, Drawing Model and latest proposal.
- `POST /api/plan` accepts `result_id`, `source_hash`, `resolutions`, and optional
  `llm`. It accepts no client-supplied CAD parameters or approval flags.
- `GET /api/design/<result>/<attempt>/list` revalidates and downloads that specific
  ready draft. The pinned attempt prevents another replan changing an older
  review's download. A changed source read invalidates the download.

Planning is a separate explicit action. With resolved evidence it adds one visual
call per page and one text call. Unresolved evidence stops before any model call.
Existing cache/provider/trace wrappers are reused. Invalid response schemas and
invalid visual references are discarded from the cache. A single local planning
request is allowed at a time; concurrent requests receive HTTP 409.

## Validation

Run `.venv\Scripts\python.exe tests\run_all.py`. Offline cases cover provenance,
equal-valued observations, metadata conflicts, corrections, safe arithmetic,
cross-page bindings, omitted/unsupported features, dependency failures, provider
errors, API requests, immutable downloads, and the existing approval pause.

With Node.js installed, also run
`.venv\Scripts\python.exe tests\check_drawing_ui.py` for the dependency-free DOM
behavior smoke test. It checks corrections, stale-download removal, request data,
and links pinned to the returned attempt; it does not test browser layout.

Live model interpretation quality and SolidWorks geometry still require separate
evaluation on reviewed real drawings. No CAD fidelity score is claimed here.
