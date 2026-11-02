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

The menu (see `app/menu/`): `extrude_profile_boss` (the base plate, or a boss on a face), `cut` (circle, slot, rectangle; blind or through),
`hole` (plain, counterbore, 90° countersink, threaded as a plain hole plus a note), `circle_boss`, `profile_cut` (any closed outline), `rib`
(triangular ribs on a round boss, optional R fillet at the feet) and `fillet_chamfer_edges` (chosen by location). Units: mm or inch.
The base is an outline extruded from the plate view (x right, y up, thickness from the back face toward the viewer). Features can go on all six faces of the
base (front, back, top, bottom, left, right; positions on the side faces are measured from the part's own edges) and on a flat face beyond a base face
(`offset`, e.g. the top of a boss). Not supported yet: patterns (list every position instead), revolved bodies, angled / slanted faces, real threads,
assemblies. Whatever is not supported is written as *unsupported* by the reader.

## Requirements

* Windows with **SolidWorks 2026** installed and licensed (the server uses its COM interface).
* **Python 3.14** (the pins were first set on 3.12; the same pins install and pass the offline tests on 3.14.8).
* A **Gemini API key** (Google AI Studio).
* To (re)build the SolidWorks execution server: Visual Studio 2022 Build Tools and the .NET Framework 4.8 targeting pack. Build it with `scripts\build_execution_layer.ps1` (the compiled copy is not in this repo).

## Set up

```powershell
python -m venv C:\path\to\venv                      # anywhere outside this folder is fine
C:\path\to\venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env                              # then put GEMINI_API_KEY in .env
powershell -ExecutionPolicy Bypass -File scripts\build_execution_layer.ps1     # required once: builds build\execution-bin from third_party
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

A saved part that stays open in SolidWorks stops the next build (only unsaved scratch parts are closed). To keep such a part open set
`SW_KEEP_OPEN_DOCS=Title1,Title2` (document titles, comma separated) before starting the job, and save the new part under a different name.

Each pause prints what you have to decide. The decisions (JSON objects) are listed in the header of `scripts\run_job.py`: approve, correct (the model reads
the drawing again with your note), edit (you change the list yourself), reject a fix, abort, retry after a model failure. A paused job can be resumed days
later: its state is saved in `storage\jobs.sqlite`.

A job writes to `storage\jobs\<id>\`: the list drafts, every approved version, the build reports and `output\` (SLDPRT, STEP, STL, preview PNG). Step-by-step
logs of the SolidWorks calls are in `logs\` and `build\logs\`.

## Folder structure

```
app\
  menu\       the build list format, the features' parameters, the menu the model reads, the checks on a list
  runner\     builds an approved list in SolidWorks (no LLM), classifies failures
  agent\      the LangGraph orchestrator: the whole job, the human pauses, the guards, the prompts
  features\   the CAD features (each: request, build steps, expected volume, position checks)
  verify\     measures every step, bounding box, checks before a run
  finish.py   save SLDPRT, export STEP + STL, preview, checks
  mcp_client\ typed wrapper of the SolidWorks MCP tools
  llm\        the model interface and the Gemini client (another model = one more class)
scripts\      run_job.py (the job command), build_execution_layer.ps1
templates\    Part_MMDS.PRTDOT, the millimetre part template (SolidWorks ships none)
third_party\  only a README: put the SolidWorks MCP server source here yourself (not in this repo, AGPL-3.0), see third_party\README.md
build\          created by scripts\build_execution_layer.ps1 (compiled execution server); not in this repo
```

## Good to know

* **Units of the files:** a part built from `templates\Part_MMDS.PRTDOT` exports STEP in millimetres. The STL is always millimetres and is written
  shifted into positive space (its size is exact, its position is not the model's).
* **Model failures:** a temporary error (503, quota) is retried once automatically; then the job pauses and you can retry or abort. The free Gemini tier has a
  daily request limit.
* **What the checks prove:** that the part matches the numbers of the approved list. Whether the list matches the drawing is the point of your approval and
  your review.
* The program refuses to start the executable that ships inside `third_party\`; it only runs the one built into `build\execution-bin\`.
* `third_party\` is the open-source SolidWorks MCP server (AGPL-3.0). Check its licence before distributing a networked product.
