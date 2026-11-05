# Guided React browser workflow

Browser uploads use the extraction agent to identify complete drawing views and
return normalized bounding boxes with the first whole-page measurement read.
These boxes drive the crop reading and frontend overlays; the browser workflow
does not run CV segmentation. Invalid or overlapping agent boundaries remain
explicit review issues rather than silently using guessed replacements.

Browser uploads return the drawing and measurements before feature interpretation
and BOM reading finish. Additional observations are stored separately under
`storage/extraction/` and appear through status polling. They do not replace
source readings or saved human corrections. A server restart marks unfinished
enrichment as interrupted. The provider and high reasoning effort are unchanged;
this reduces the wait before reviewing measurements, not the provider's thinking
time. Simultaneous identical cached requests share one provider call.

Open `http://127.0.0.1:8765`. The React interface guides the user through five
pages: upload, extraction and measurement review, SolidWorks build, 2D/3D
verification, and export. Processing overlays identify long-running extraction,
assembly-read and CAD-build actions. The underlying extraction and CAD APIs are
unchanged. Part, assembly and mixed component sheets use the same guided flow.

The extraction page groups the agent's feature output, source views, dimensions,
assembly component questions and **Agent chat** together. Chat accepts replies
for a specific measurement, an unreadable source callout or a general drawing
clarification. Replies are saved with the original source and update the existing
review layer. They do not build, approve or export a model. A changed review
invalidates the old plan and preview. Unsaved workspace edits must be saved before
applying a chat reply, so typing in chat cannot discard those edits.

React UI source is `app/web/ui.jsx`; rebuild the locally bundled runtime with
`npm install` and `npm run build:ui`. The local bundle is served from
`app/web/ui.js`; the page does not load React from a third-party CDN.
The shared visual system lives in `app/web/studio.css` and is bundled as `ui.css`.
The review page starts with the source image and measurement editor, followed by
agent questions, component review and expandable feature evidence. Session
statistics are secondary details. Proposed plans and build approval live on the
Build page; a successful part planning request takes the user there automatically.

If view detection has been updated, expand **Source checks and processing
details** and choose **Re-extract with current view detection**. This reads the
original upload again into a new result; earlier readings, reviews and approvals
are retained separately and are not carried over to changed evidence.

1. The extraction worker reads dimensions, views and physical feature
   observations. Assembly/component sheets also trigger whole-sheet BOM reading.
2. Review all readings beside the original pages. Confirm ambiguous observations,
   correct conflicting values and answer unreadable-callout questions. Original
   readings remain immutable; explicit human clarifications and supplied values
   are stored separately with their page/view provenance.
3. The SolidWorks worker plans from that reviewed evidence. Unresolved questions
   return to the same page. For assemblies, review quantities, component identity,
   native component files, placements, mates and final dimensional expectations
   inline. Missing geometry or dimensions require source information; selecting
   an assembly does not turn off extraction or navigate elsewhere.
4. Approve the exact part or assembly plan to build. Part repair proposals pause
   for confirmation or corrected feature values. Assembly verification failures
   return questions for review. Revised plans require a fresh build and review.
5. Inspect the rotatable mesh beside the original 2D pages. Front/top/right and
   isometric views are available. Confirm both drawing comparison and measurement
   verification. A request for correction keeps exports locked.
6. After confirmation, choose **Export confirmed model**. Part downloads are
   SLDPRT, STEP and STL. Assembly downloads include SLDASM, STEP, STL and a ZIP with
   the native assembly and its pinned component files. Transfer the full component
   bundle when sharing the assembly.

## Persistence and validation

Jobs live in `storage/workflow/`. The browser bridge uses the existing checkpointed
part graph and checked assembly runner. Both workers retain the selected provider;
Domo requests high effort. Native execution shares the existing exclusive CAD
lease and intent journal. A stale revision, changed measurement review, changed
plan or altered private model invalidates the corresponding action. Interrupted
native work is never silently replayed after a server restart.

Before confirmation the browser saves a private native snapshot and tessellation
for preview under the job's `private/` directory. STEP and downloadable deliverables
are prepared only after acceptance and the separate export action. Final export
checks the active saved document, configuration, unsaved-change flag and source
hashes, then validates output units, mesh size and hashes. Private mesh geometry
is sent to the browser to render the preview; it is not a final export package.

The existing CLI commands and legacy APIs remain available for compatibility;
their original export timing is unchanged. The browser does not send users to
them to complete this flow.

## Current geometric limits

The workflow handles both document types, but does not add support for every
mechanical feature. The automatic part compiler currently covers rectangular
plates, plain holes and circular bosses. Other features remain explicit questions
or unsupported geometry. The checked assembly runner requires reviewed flat leaf
component models, placements and mates; it cannot invent internal component
geometry from an undimensioned general arrangement.

Comparing the original drawing with the generated model is supported here.
Automatic reconstruction of all source annotations, section/detail views and BOM
balloons into a native 2D sheet remains outside this workflow. The separately
reviewed native sheet runner remains available. Runtime behavior still requires
working SolidWorks COM activation; simulated tests do not establish native CAD
accuracy.
