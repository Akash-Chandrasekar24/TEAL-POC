# Drawing to SolidWorks

Give it an engineering drawing (an image or a PDF page). It reads the drawing with a language model, shows you a **build list**, and after you approve
it builds the part in SolidWorks and returns `.SLDPRT`, `.STEP`, `.STL` and a preview picture. A human approves every list and every suggested fix,
and reviews the finished part beside the drawing.

```
drawing ─► read (LLM) ─► validate (code) ─► YOU approve the list ─► build (code, in SolidWorks) ─► files ─► YOU review
                                                                        │ a step fails
                                                                        ▼
                                                   suggest a fix (LLM) ─► YOU approve the exact change ─► rebuild
```

* The language model only **reads the drawing** and **suggests a fix** for a failed step. It never touches SolidWorks.
* **Code builds the part** from the approved list, one feature at a time, and measures the result after every step (volume, positions, faces). A wrong
  step stops the build.
* The approved list is locked. A suggested fix can change only the parameters of the failed item, and only after you approve the exact difference.
* Anything the menu cannot build is marked *unsupported* and goes to a human. Nothing is approximated.

## What it can build

Native model-linked **2D drawing sheets** now have a separate reviewed workflow:
standard views and source-bound dimension readbacks from saved SLDPRT or accepted
SLDASM output, followed by SLDDRW/PDF export and human review. Source sections,
BOMs and other unimplemented annotations remain explicit blockers. See
[Native drawing sheets](docs/NATIVE_DRAWING_SHEETS.md).

The existing workflows now run under an orchestrator with two workers:
**ExtractionAgent** reads drawing/BOM evidence, and **SolidWorksAgent** handles
planning, repair proposals and the existing CAD execution. Both default to the
configured Domo model. Existing approval, validation, caching and export behavior
is retained. See [Agent workflow](docs/AGENT_WORKFLOW.md) for ownership, monitoring
and compatibility details.

Extraction uses medium reasoning effort (`EXTRACTION_REASONING_EFFORT=medium`);
CAD planning and repairs retain high effort (`CAD_REASONING_EFFORT=high`).
Independent pages are read two at a time, and completed pages stream to the
loading screen. Feature and BOM interpretation continue in the background while
the measurement review is available. These settings preserve the independent
whole-page/crop checks and human approval gates. Provider latency still affects
total processing time; four pages are not guaranteed to finish in one page's time.

The [bounded ReAct harness](docs/AGENT_HARNESS.md) adds worker action limits,
checkpointed model budgets, measured repair feedback, and a shared native CAD
writer lease. Inspect its configured capabilities at `/api/harness`.

An initial **assembly workflow** is also available from the drawing reader's
**Open assembly planner** link. It reads multipage BOMs, tracks supplied/custom
SLDPRT models, validates explicit placements and mates, and preserves approval
before checked execution. See [Assembly workflow](docs/ASSEMBLY_WORKFLOW.md) for
the browser/CLI steps and current limits. Planning works without SolidWorks;
native build and geometric checks require a licensed installation. Complex
assembly accuracy has not yet been validated on real reference models.

The menu (see `app/menu/`): `extrude_profile_boss` (the base plate, or a boss on a face), `cut` (circle, slot, rectangle; blind or through),
`hole` (plain, counterbore, 90° countersink, threaded as a plain hole plus a note), `circle_boss`, `profile_cut` (any closed outline), `rib`
(triangular ribs on a round boss, optional R fillet at the feet), `fillet_chamfer_edges` (chosen by location), `revolve_base` (a turned part from
its half-section; nothing can follow it yet) and `revolve_on_face` (a bend or ring revolved on a face). Repeated features use the item's `pattern`
(linear, grid, mirror, circular / bolt circle). Units: mm or inch.
The base is an outline extruded from the plate view (x right, y up, thickness from the back face toward the viewer). Features can go on all six faces of the
base (front, back, top, bottom, left, right; positions on the side faces are measured from the part's own edges) and on a flat face beyond a base face
(`offset`, e.g. the top of a boss). Not supported yet: a side profile that varies (two-view intersection), features on angled / slanted faces or
datum planes, angled holes, sweeps, shells, drafts, real threads, and general assembly reconstruction (see `docs\System_Architecture.docx`, phase P5). The part reader writes unsupported geometry as *unsupported*; the separate initial assembly workflow requires reviewed component files, placements and mates.

## Requirements

* Windows with **SolidWorks 2026** installed and licensed (the server uses its COM interface).
* **Python 3.14** (the pins were first set on 3.12; the same pins install and pass the offline tests on 3.14.8).
* A model: a **Gemini API key** (Google AI Studio) **or** a **Domo** instance + developer token (Domo AI Service Layer). Choose with
  `WORKER_LLM_PROVIDER` in `.env` (default `domo`) or `--llm gemini|domo` on `run_job.py`; a resumed job keeps the model it was started with. Domo reads pictures
  only (PNG / JPEG / WebP; a picture over 2048 px is shrunk), so give it a PNG export of a PDF drawing.
* The SolidWorks backend is the pinned [SolidPilot MCP repository](https://github.com/eyfel/mcp-server-solidworks), with our custom features layered on top.
  Setup fetches its source and builds our own executable. MSBuild is auto-discovered; .NET 4.8 reference assemblies are restored inside `build\`.
  See [backend setup](docs/SOLIDWORKS_BACKEND.md). Offline MCP discovery does not require an activated SolidWorks session; CAD execution does.

## Set up

```powershell
python -m venv C:\path\to\venv                      # anywhere outside this folder is fine
C:\path\to\venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env                              # then put GEMINI_API_KEY in .env
python scripts\setup_solidworks_backend.py --fetch --build --probe    # pinned source + build + MCP compatibility check
```

## Run a job

Close other programs that use the SolidWorks MCP server (port 5000 must be free) and do not work in SolidWorks while a job builds.

```powershell
python scripts\run_job.py start --image drawing.png --job-id my_part      # reads the drawing, then waits for you
python scripts\run_job.py resume --job my_part --decision "{\"action\": \"approve\"}"
python scripts\run_job.py resume --job my_part --decision "{\"action\": \"accept\"}"     # after you reviewed the finished part
python scripts\run_job.py status --job my_part
python scripts\run_job.py start --list approved_list.json --job-id replay   # rebuild a saved list: no model call, then validate -> your approval
```

Repeated model calls are answered from a cache in `storage\cache\` (same model, prompt, drawing and correction = same answer, no tokens). The
cache never skips your approval. `--no-cache` forces real calls; `python scripts\run_job.py cache` shows it, `cache --purge` empties it. The design is in
`docs\System_Architecture.docx` (section 4.7). Tests: `python tests\test_cache.py`, `tests\test_llm_domo.py`, `tests\test_cache_job.py` (needs the requirements).

A saved part that stays open in SolidWorks stops the next build (only unsaved scratch parts are closed). To keep such a part open set
`SW_KEEP_OPEN_DOCS=Title1,Title2` (document titles, comma separated) before starting the job, and save the new part under a different name.

Each pause prints what you have to decide. The decisions (JSON objects) are listed in the header of `scripts\run_job.py`: approve, correct (the model reads
the drawing again with your note), edit (you change the list yourself), reject a fix, abort, retry after a model failure. A paused job can be resumed days
later: its state is saved in `storage\jobs.sqlite`.

A job writes to `storage\jobs\<id>\`: the list drafts, every approved version, the build reports and `output\` (SLDPRT, STEP, STL, preview PNG). Step-by-step
logs of the SolidWorks calls are in `logs\` and `build\logs\`.

## Browser workflow

```powershell
.venv\Scripts\python -m app.web.server          # then open http://127.0.0.1:8765
```

Upload 1–6 files for one design, select the model and choose **Extract features and
measurements**. Both part and assembly drawings stay in this one page: extraction,
human corrections, reviewed build approval, clarification pauses, 2D/3D comparison
and export after final confirmation. Assembly sheets automatically trigger BOM
extraction and show component/model/placement questions inline. No CLI handoff is
needed. Extraction/review work without SolidWorks; building requires it.

Results reopen at `http://127.0.0.1:8765/#<id>`. See
[Browser workflow](docs/BROWSER_WORKFLOW.md) for the review/export gates and current
geometric limits. Agreement between reads is not an engineering accuracy score.

## Review dimensions and generate a build-list draft

The browser compiles reviewed dimensions into a provenance-bound plan, then asks
for explicit approval before building in SolidWorks. The initial part compiler
supports rectangular plates, plain holes and round bosses. Unresolved measurements
and unsupported geometry pause the flow. Existing build-list APIs and CLI commands
remain available for compatibility.
See [Drawing planning](docs/DRAWING_PLANNING.md) for the workflow, limits, and API.

## Debugging: the process log of every run

Every run (web page, `scripts\views.py`, every `run_job.py` command) writes a detailed log:

| Where | What |
|---|---|
| `storage\runs\<id>\` (web), `<out>\trace\` (views.py), `storage\jobs\<job>\trace\<time>_<command>\` (jobs) | the run's folder |
| `trace.jsonl` | one JSON event per step: time, stage, step, level (info / warn / error) and every detail |
| `run.log` | the same, as readable lines |
| `files\` | exactly what the model was **shown** (every picture sent, numbered per call) and what it **answered** (every raw reply) |

Stages: `ingest` (file type from the bytes, pages, DPI, cache hit), `segment` (boxes, tables left out, cache hit), `pack` (sheets, crop scale; a
warning when crops need more than one sheet), `llm` (each call: prompt, pictures, cache hit, tokens, seconds, reply file; a warning when Domo had
to shrink a picture), `read` (each crop's name and dimensions; warnings when a reply misses or invents a crop), `combine` (agreed / check / no
view), `run` (summary; on failure the error and its traceback). In the web page the **Process log** panel shows it with links to those files;
a failed run's error message links to its log. Secrets are never logged. `TRACE_FILES=off` in `.env` stops keeping the pictures and replies.

## Develop: tests, benchmark, views (no SolidWorks needed)

```powershell
python -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python tests\run_all.py                         # every offline test (fake model, fake SolidWorks build)
.venv\Scripts\python eval\run.py --segment                    # benchmark: are the views of each drawing found? (code only)
.venv\Scripts\python eval\run.py --read --llm gemini          # benchmark: dimensions found by today's reader (needs a model key)
.venv\Scripts\python scripts\views.py set.pdf page2.png --out out [--read]    # views of a 1-6 page set (P2); --read: dimensions per view (P3, 2 model calls a page)
.venv\Scripts\python eval\run.py --p3 --llm domo               # benchmark the P3 reader: recall, precision, calls, tokens
```

The benchmark is `eval\benchmark.json` (11 drawings from `inputs\` + 2 synthetic 2-page sets made by `eval\make_synthetic.py`). Its truth
(views, dimensions) was DRAFTED from the pictures and is marked `verified: false`: an engineer must check it before the scores are trusted.
Results and overlay pictures go to `eval\runs\<time>\`. The phases are described in `docs\System_Architecture.docx`.

## Folder structure

```
app\
  menu\       the build list format, the features' parameters, the menu the model reads, the checks on a list
  runner\     builds an approved list in SolidWorks (no LLM), classifies failures
  agent\      the LangGraph orchestrator: the whole job, the human pauses, the guards, the prompts
  features\   the CAD features (each: request, build steps, expected volume, position checks)
  verify\     measures every step, bounding box, checks before a run
  finish.py   save SLDPRT, export STEP + STL, preview, checks
  mcp_client\ upstream MCP integration, source/build checks, typed native tools; custom features use this backend
  llm\        the model interface, the Gemini and Domo clients, the cached client, make_client (another model = one more class)
  cache.py    the cache: content-addressed blob store + index (storage\cache)
  ingest\     drawing files -> pages (PDF rendering; the file type is read from the bytes)
  perception\ segment.py: the views on a page (code); extract.py: dimensions + view names (model, 2 calls a page)
eval\         the benchmark, its scorers and runner
tests\        offline tests (run_all.py)
scripts\      run_job.py (the job command), build_execution_layer.ps1
templates\    Part_MMDS.PRTDOT, the millimetre part template (SolidWorks ships none)
third_party\  dependency lock + README; setup fetches the pinned SolidWorks MCP source here (checkout not committed, AGPL-3.0)
build\          created by scripts\build_execution_layer.ps1 (compiled execution server); not in this repo
```

## Good to know

CAD capability development: see [docs/CAD_DEVELOPMENT.md](docs/CAD_DEVELOPMENT.md)
for the implementation milestones and live acceptance cases. Generate the current
coverage inventory with `.venv\Scripts\python.exe -m eval.cad --out docs/cad-coverage.md`.
It reports reviewed requirements and reference-list validity separately from live
CAD coverage; draft drawing assessments are in `eval/cad_requirements.json`.

R1/R2 OCR-assisted extraction trials are available in the upload page's
Extraction selector. R1 combines OCR and visual assessment; R2 adds a visual
verification pass. The existing reviewed workflow remains the default. These
are evaluation modes: their S2 evidence and original view crops are retained,
but they cannot start or export CAD until engineering acceptance is implemented.
Extraction uses medium effort; the existing CAD worker remains high.

The local OCR adapter uses installed Windows OCR and an available `en-US`
language pack. It launches a hidden PowerShell helper with a process-only
execution-policy exception; it does not change machine policy. It returns
positioned readings, not certified engineering interpretations or invented
confidence scores. No additional OCR package or cloud OCR service is required.
The helper and recognition engine are reused between pages. The server warms
them on a blank tile during idle startup; supported RGB PNGs bypass redundant
decode/re-encode work. Domo transports also reuse connections while each request
retains its own model, effort and evidence. Drawing-result caching is separate.

Run a fresh, uncached comparison with:

```powershell
.venv\Scripts\python.exe eval/compare_extraction.py inputs/image_9.png --reference eval/hybrid_support_block_reference.json
```

Read the [measured R3 results](docs/R3_EXTRACTION_RESULTS.md) before interpreting
the scores as general accuracy. The real-image inventories were transcribed by
the assistant and still need independent engineer review. The controlled sheet
has machine-generated labels. Existing provider credentials remain in `.env`.

The [30-second enhancement measurements](docs/EXTRACTION_SPEED_ENHANCEMENTS.md)
include unsuccessful trials. Shorter prompts, OCR anchors and parallel feature
surveys are benchmark-only options (`--transport lean|anchored|split`); the
validated verbose R1/R2 transport and existing reviewed workflow remain defaults.
Use `--repeat 3 --warm-ocr --strategies r1` to test fresh model calls against an
initialized server equivalent. Initialization is outside those warm-request
timings; no drawing result is precomputed. These changes do **not** establish a
reliable under-30-second SLA, or the 40-second allowance on dense drawings.

* **Units of the files:** a part built from `templates\Part_MMDS.PRTDOT` exports STEP in millimetres. The STL is always millimetres and is written
  shifted into positive space (its size is exact, its position is not the model's).
* **Model failures:** a temporary error (503, quota) is retried once automatically; then the job pauses and you can retry or abort. The free Gemini tier has a
  daily request limit.
* **What the checks prove:** that the part matches the numbers of the approved list. Whether the list matches the drawing is the point of your approval and
  your review.
* The program refuses to start the executable that ships inside `third_party\`; it only runs the one built into `build\execution-bin\`.
* `third_party\` is the open-source SolidWorks MCP server (AGPL-3.0). Check its licence before distributing a networked product.
