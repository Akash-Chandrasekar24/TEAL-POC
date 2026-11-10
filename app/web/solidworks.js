"use strict";
// Everything SolidWorks in the browser: the SolidWorks page (status, parts and their build lists, "Build everything", the one approval,
// per-part builds), the build job card (progress, fix approvals, model check, export) and the request helpers they share.
// Loaded before the main page script; it uses that script's helpers (el, $, state, show ...) only when called.

// ---- The SolidWorks page (step 3): parts and build lists on top, the current build job (progress, check, export) under them
function solidworksPage(r) {
  const parts = partsPanel(r);
  if (typeof fetch === "function") parts.load();
  return el("div", { id: "solidworks-page" }, parts, el("div", { id: "workflow-job" }, workflowCard(r)));
}

// ---- "Build everything in SolidWorks": one button -> build lists for every part -> ONE approval -> every part built in turn
const BATCH_ITEM = { to_prepare: "Waiting for its build list", preparing: "Writing its build list…", listed: "Build list ready", list_failed: "Build list failed",
  no_views: "Read from the assembly views", queued: "Queued", building: "Building in SolidWorks…", awaiting_review: "Built: compare it with the drawing",
  accepted: "Accepted", exported: "Exported", awaiting_fix_approval: "Needs your OK for a fix", awaiting_approval: "Stopped before SolidWorks", needs_human: "Needs your attention",
  llm_failed: "Model unavailable: retry", build_failed: "Build failed", not_buildable: "Not buildable yet", not_approved: "Not approved" };

// The section of parts with doubts: one button hands every doubt of every part to the AI, or each line is resolved on its own.
function doubtSection(r, blocked, post) {
  const panels = blocked.map((p) => ({ p, panel: resolvePanel(r, p, post) }));
  const all = el("button", { type: "button", class: "btn", text: `Let AI handle every doubt (${blocked.length} part${blocked.length === 1 ? "" : "s"})` });
  all.addEventListener("click", () => { all.disabled = true; post("/api/parts/ai-resolve", { part_ids: blocked.map((p) => p.id) }, "Handing every doubt to the AI"); });
  return el("div", { class: "doubt-section" },
    el("div", { class: "doubt-head" }, el("div", {}, el("b", { text: `${blocked.length} part${blocked.length === 1 ? "" : "s"} with doubts` }),
      el("small", { class: "hint", text: " Answer a doubt yourself, or let the AI decide. Every size the AI decides is marked ASSUMED, and nothing is built until you approve." })), all),
    panels.map(({ p, panel }) => el("div", { class: "doubt-part" }, el("b", { class: "doubt-part-name", text: p.name }), panel)));
}

// ---- THE SolidWorks button: one uninterrupted flow (build lists -> AI decides any doubt -> every part built -> one assembly)
const FLOW_ITEM = { to_prepare: "Waiting", preparing: "Writing its build list…", listed: "Build list ready", queued: "Waiting for SolidWorks", building: "Building in SolidWorks…",
  awaiting_review: "Built", accepted: "Built", exported: "Built", not_buildable: "Not built", list_failed: "Not built", build_failed: "Not built",
  needs_human: "Not built", awaiting_fix_approval: "Not built", llm_failed: "Not built" };

function flowBox(r, data, reload, status) {
  const batch = data.batch, sw = data.solidworks || {};
  const running = batch && batch.flow === "one_button" && ["preparing", "resolving", "building", "assembling"].includes(batch.phase);
  const start = el("button", { type: "button", class: "btn build-all", text: batch?.flow === "one_button" && batch.phase === "done" ? "Build in SolidWorks again" : "Build in SolidWorks",
    disabled: running || !sw.ready });
  start.addEventListener("click", async () => {
    start.disabled = true;
    try { await workflowRequest("/api/parts/build-in-solidworks", { result_id: r.id, llm: ($("#llm") && $("#llm").value) || r.llm }); await reload(); }
    catch (error) { status.textContent = error.message; start.disabled = false; }
  });
  const items = (batch?.flow === "one_button" ? batch.items : []) || [];
  const phaseOf = (it) => it.job?.phase || it.status;
  const built = items.filter((it) => ["awaiting_review", "accepted", "exported"].includes(phaseOf(it)));
  const rows = items.map((it) => {
    const ph = phaseOf(it), ok = ["awaiting_review", "accepted", "exported"].includes(ph), bad = FLOW_ITEM[ph] === "Not built";
    const row = el("li", { class: `flow-item ${ok ? "ok" : bad ? "bad" : "busy"}` }, el("b", { text: it.name }), el("span", { text: FLOW_ITEM[ph] || ph }));
    const why = it.job?.detail || it.detail;
    if (bad && why) row.append(el("small", { text: why.slice(0, 220) }));
    if (it.job && ok) row.append(el("button", { type: "button", class: "m-link", text: "Open", onclick: () => { r.workflowJob = it.job; window.dispatchEvent(new CustomEvent("studio:job", { detail: { job: it.job } })); show(r); pollJob(r); } }));
    return row;
  });
  const asm = batch?.assembly;
  const asmLine = asm ? el("p", { class: `flow-asm ${asm.status === "awaiting_review" || asm.status === "accepted" ? "ok" : ["building", "placing"].includes(asm.status) ? "busy" : "bad"}`,
    text: asm.status === "placing" ? "Placing the parts into one assembly…" : asm.status === "building" ? "Building the assembly in SolidWorks…" :
      ["awaiting_review", "accepted", "exported"].includes(asm.status) ? "Assembly built: one SolidWorks file for the whole drawing" : `Assembly not built: ${(asm.detail || asm.status).slice(0, 220)}` }) : null;
  const doneN = items.filter((it) => !["to_prepare", "preparing", "listed", "queued", "building"].includes(phaseOf(it))).length;
  const head = running ? el("div", { class: "batch-progress" }, el("span", { class: "spinner", "aria-hidden": "true" }),
      el("b", { text: batch.phase === "preparing" ? "Preparing every part" : batch.phase === "assembling" ? "Assembling" : `Building in SolidWorks: ${doneN} of ${items.length}` }))
    : el("p", { class: "hint", text: !sw.ready ? `SolidWorks is not ready: ${sw.detail || ""}` : batch?.flow === "one_button" && batch.phase === "done"
      ? `Done: ${built.length} of ${items.length} part${items.length === 1 ? "" : "s"} built.${batch.error ? " " + batch.error : ""}`
      : "One click builds the whole drawing: the build steps for every part, the parts in SolidWorks and, for several parts, one assembly. No further questions: the doubts were settled on the Review page." });
  return el("div", { class: `batch-box flow${running ? " running" : ""}` }, head, el("div", { class: "part-actions" }, start),
    rows.length ? el("ol", { class: "batch-list" }, rows) : null, asmLine);
}

function batchBox(r, data, reload, status, partPost) {
  const batch = data.batch, sw = data.solidworks || {};
  const post = async (path, body) => {
    try { await workflowRequest(path, { result_id: r.id, llm: ($("#llm") && $("#llm").value) || r.llm, ...body }); await reload(); }
    catch (error) { status.textContent = error.message; }
  };
  const start = el("button", { type: "button", class: "btn build-all", text: "Build everything in SolidWorks",
    onclick: () => { start.disabled = true; post("/api/parts/build-all", {}); } });
  const items = (batch?.items || []).map((it) => {
    const phase = it.job?.phase || it.status;
    const row = el("li", { class: `batch-item ${phase}` }, el("b", { text: it.name }), el("span", { text: BATCH_ITEM[phase] || phase }));
    if (it.detail && !["listed", "queued"].includes(phase)) row.append(el("small", { text: it.detail }));
    if (it.job) row.append(el("button", { type: "button", class: "m-link", text: phase === "awaiting_review" ? "Compare and accept" : "Open",
      onclick: () => { r.workflowJob = it.job; window.dispatchEvent(new CustomEvent("studio:job", { detail: { job: it.job } })); show(r); window.studioNavigate?.(3); pollJob(r); } }));
    return row;
  });
  const list = items.length ? el("ol", { class: "batch-list" }, items) : null;
  if (!batch || batch.phase === "done" || batch.phase === "interrupted") {
    const done = batch?.phase === "done";
    if (batch?.phase === "interrupted") start.textContent = "Continue: build everything in SolidWorks";
    return el("div", { class: "batch-box" },
      el("p", { class: "hint", text: batch?.phase === "interrupted" ? "The last run stopped when the server restarted. Build lists already written are kept; continue to write the rest." :
        done ? "Last run finished. Open each built part to compare it with the drawing; start again after changing build lists." :
        "One button: the AI writes a build list for every part that has none, you approve all of them once, then SolidWorks builds the parts one after another." }),
      el("div", { class: "part-actions" }, start), list);
  }
  if (batch.phase === "preparing" || batch.phase === "building") {
    const total = batch.items.filter((it) => batch.phase === "preparing" ? ["to_prepare", "preparing", "listed", "list_failed"].includes(it.status)
                                                                          : it.list_id).length;
    const now = batch.items.find((it) => ["preparing", "building"].includes(it.status));
    const doneN = batch.items.filter((it) => batch.phase === "preparing" ? ["listed", "list_failed"].includes(it.status) : it.list_id && it.status !== "queued" && it.status !== "building").length;
    return el("div", { class: "batch-box running" }, el("div", { class: "batch-progress" }, el("span", { class: "spinner", "aria-hidden": "true" }),
      el("b", { text: batch.phase === "preparing" ? `Writing build lists: ${doneN} of ${total}` : `Building in SolidWorks: ${doneN} of ${total}` }),
      el("span", { text: batch.phase === "preparing" ? " · up to 4 at a time, about 1–2 min each" : now ? ` · ${now.name}` : "" })), list);
  }
  // awaiting_approval: the single human approval for the whole sheet
  const ready = (data.parts || []).filter((p) => p.list?.check?.status === "ready");
  const blocked = (data.parts || []).filter((p) => !(p.list?.check?.status === "ready"));
  const gaps = ready.flatMap((p) => (p.list.check.unsupported || []).map((u) => ({ part: p, u })));
  const accept = el("input", { type: "checkbox", "aria-label": "Accept every not-modelled item" });
  const approve = el("button", { type: "button", class: "btn", text: `Approve all and build in SolidWorks (${ready.length} part${ready.length === 1 ? "" : "s"})` });
  const why = el("small", { class: "part-why" });
  const sync = () => {
    approve.disabled = !ready.length || !sw.ready || (gaps.length && !accept.checked);
    why.textContent = !ready.length ? "No part has a buildable list yet." : !sw.ready ? `SolidWorks is not ready: ${sw.detail || ""}` : gaps.length && !accept.checked ? "Tick the box to accept the not-modelled items above." : "";
  };
  accept.addEventListener("change", sync); sync();
  approve.addEventListener("click", () => {
    approve.disabled = true;
    const accepted = Object.fromEntries(ready.map((p) => [p.id, (p.list.check.unsupported || []).map((u) => u.id)]));
    post("/api/parts/approve-all", { batch_id: batch.id, accepted_gaps: accepted });
  });
  return el("div", { class: "batch-box approve" },
    el("h4", { text: "Approve the build" }),
    el("p", { class: "hint", text: `${ready.length} part${ready.length === 1 ? "" : "s"} will be built in SolidWorks, one after another. You then compare each finished model with the drawing on this page.` }),
    el("ul", { class: "batch-ready" }, ready.map((p) => el("li", {}, el("b", { text: p.name }), ` · ${(p.list.draft.items || []).length} features`))),
    gaps.length ? el("div", { class: "part-gaps" }, el("b", { text: `Not modelled (${gaps.length})` }), el("ul", {}, gaps.map(({ part, u }) => el("li", { text: `${part.name}: ${u.text}` }))),
      el("label", {}, accept, " I accept every not-modelled item listed above")) : null,
    blocked.length ? doubtSection(r, blocked, partPost) : null,
    el("div", { class: "part-actions" }, approve, why),
    el("div", { class: "part-actions" }, el("button", { type: "button", class: "m-link", text: "Start again", onclick: () => post("/api/parts/build-all", {}) })));
}

// ---- A part that cannot be built yet: why, in plain words, and the way forward: answer the AI's questions, or let the AI work the sizes out
function blockedReasons(part) {
  const list = part.list, check = list?.check || {}, draft = list?.draft || {};
  if (!list) return [part.views.length ? "No build list has been written yet." : "This part is not drawn on its own on the sheet: it only appears inside the assembly views."];
  if (check.status === "stopped") return [`The AI stopped: ${check.blocking?.[0] || "it could not list the part"}.`];
  const out = [];
  for (const b of check.blocking || []) {
    if (/^needs_human is not empty/.test(b)) { const n = (draft.needs_human || []).length; out.push(`The AI has ${n} question${n === 1 ? "" : "s"} for you.`); }
    else if (/^uncertain items/.test(b)) { const n = (draft.items || []).filter((i) => i.uncertain).length; out.push(`${n} step${n === 1 ? " uses" : "s use"} sizes that are not printed and need your confirmation.`); }
    else if (/the list has no items/.test(b)) out.push("The AI could not write any 3D steps from what is printed.");
    else if (/projection is unknown/.test(b)) out.push("The drawing's projection is not known.");
    else if (/not in seen_dimensions/.test(b)) out.push(`A number is not printed on the sheet: ${b.split(" is not")[0]}.`);
    else out.push(b);
  }
  return [...new Set(out)];
}

// The part's doubts, one line each: the AI's questions, else its assumed steps, else why there is no list at all.
function doubtsOf(part) {
  const list = part.list, draft = list?.draft || {}, check = list?.check || {};
  const questions = (draft.needs_human || []).map((q) => ({ text: q, kind: (draft.items || []).length ? "question" : "empty-q" }));
  if (!list || check.status === "stopped" || check.status === "invalid" || (!(draft.items || []).length && !questions.length))
    return [{ text: blockedReasons(part)[0], kind: "empty" }];
  const out = questions;
  if (!out.length) out.push(...(draft.items || []).filter((i) => i.uncertain).map((i) => ({ text: `${i.printed || i.feature}: ${(i.derived_from || "size not printed").replace(/^ASSUMED:\s*/i, "assumed ")}`, kind: "uncertain" })));
  const other = (check.blocking || []).filter((b) => !/^needs_human is not empty|^uncertain items|the list has no items/.test(b));
  out.push(...other.map((b) => ({ text: blockedReasons({ ...part, list: { ...list, check: { ...check, blocking: [b] } } })[0], kind: "other" })));
  return out;
}

const AI_DECIDES = "AI decides";

// Resolve the doubts: every one answered by the designer or handed to the AI, then ONE request. All handed to the AI and nothing is
// missing -> the list is confirmed as written (no model call); otherwise the AI rewrites it with the answers.
function applyDoubts(part, rows, post) {
  const mine = rows.filter((x) => x.mode === "human" && x.box.value.trim()).map((x) => ({ question: x.d.text, answer: x.box.value.trim() }));
  const ai = rows.filter((x) => x.mode === "ai").map((x) => ({ question: x.d.text, answer: AI_DECIDES }));
  if (!mine.length) return post("/api/parts/ai-resolve", { part_ids: [part.id] }, `Handing ${part.name}'s doubts to the AI`);   // all to the AI: background
  return post("/api/parts/create", { part_id: part.id, answers: [...mine, ...ai], work_it_out: ai.length > 0 }, `Rewriting the build list for ${part.name}`);
}

function resolvePanel(r, part, post) {
  const rows = doubtsOf(part).map((d) => ({ d, mode: null, box: el("textarea", { class: "q-input doubt-box", rows: "2", maxlength: 600, hidden: true,
    placeholder: d.kind === "empty" ? "Main sizes or a short description, e.g. \"fits the Ø120 cavity, 168 long\"" : "Your answer", "aria-label": `Answer: ${d.text}` }) }));
  const apply = el("button", { type: "button", class: "btn", onclick: () => { apply.disabled = true; applyDoubts(part, rows, post); } });
  const progress = el("small", { class: "hint" });
  const sync = () => {
    const done = rows.filter((x) => x.mode === "ai" || (x.mode === "human" && x.box.value.trim())).length;
    apply.disabled = done < rows.length;
    apply.textContent = "Apply";
    progress.textContent = done < rows.length ? `${done} of ${rows.length} resolved` : "Ready to apply";
    rows.forEach((x) => { x.li.dataset.mode = x.mode || ""; x.humanBtn.className = `doubt-btn${x.mode === "human" ? " on" : ""}`; x.aiBtn.className = `doubt-btn ai${x.mode === "ai" ? " on" : ""}`; });
  };
  rows.forEach((x) => {
    x.humanBtn = el("button", { type: "button", text: "Answer myself", onclick: () => { x.mode = "human"; x.box.hidden = false; x.box.focus?.(); sync(); } });
    x.aiBtn = el("button", { type: "button", text: AI_DECIDES, onclick: () => { x.mode = "ai"; x.box.hidden = true; sync(); } });
    x.box.addEventListener("input", sync);
    x.li = el("li", { class: "doubt" }, el("div", { class: "doubt-line" }, el("span", { class: "doubt-text", text: x.d.text }),
      el("span", { class: "doubt-actions" }, x.humanBtn, x.aiBtn)), x.box);
  });
  sync();
  const panel = el("div", { class: "resolve" }, el("ol", { class: "doubts" }, rows.map((x) => x.li)), el("div", { class: "part-actions" }, apply, progress));
  return panel;
}

// ---- One SolidWorks file for the whole sheet: the built parts, placed by the AI from the assembly views, approved once, then built
const AXIS_WORD = (rot) => {
  const names = ["X", "Y", "Z"], row = (i) => rot.slice(3 * i, 3 * i + 3), name = (v) => { const k = v.findIndex((c) => Math.abs(c) > 0.5); return `${v[k] < 0 ? "−" : "+"}${names[k]}`; };
  return `x→${name(row(0))}, y→${name(row(1))}`;
};

function assemblyBox(r, data, post) {
  const asm = data.assembly, batch = data.batch || {}, state = batch.assembly || {};
  const built = (batch.items || []).filter((i) => ["awaiting_review", "accepted", "exported"].includes(i.job?.phase || i.status));
  if (!built.length && !asm) return null;
  const head = el("div", { class: "asm-head" }, el("div", {}, el("h4", { text: "One SolidWorks file for the whole drawing" }),
    el("small", { class: "hint", text: "The parts built above become the components; the AI places each one from the assembly and section views. You approve the placement once, then SolidWorks builds the assembly." })));
  const prepare = el("button", { type: "button", class: asm ? "m-link" : "btn", text: asm ? "Place the parts again" : `Prepare the assembly (${built.length} built part${built.length === 1 ? "" : "s"})`,
    onclick: () => post("/api/parts/assemble", {}, "The AI is placing the parts from the assembly views") });
  if (!asm || !asm.frame) return el("div", { class: "asm-box" }, head, el("div", { class: "part-actions" }, prepare));
  const plan = asm.plan || {}, names = Object.fromEntries((plan.bom || []).map((b) => [b.id, b.description]));
  const rows = (plan.occurrences || []).map((o) => el("tr", {}, el("td", { text: names[o.bom_id] || o.bom_id }), el("td", { text: o.id.split("-").pop() }),
    el("td", { text: (o.placement?.translation || []).map((v) => Math.round(v * 10) / 10).join(", ") }), el("td", { text: o.placement ? AXIS_WORD(o.placement.rotation) : "" }),
    el("td", { class: "asm-ev", text: o.placement?.evidence || "" })));
  const status = state.status === "building" ? "Building the assembly in SolidWorks…" : state.status ? `Assembly: ${BATCH_ITEM[asm.job?.phase || state.status] || asm.job?.phase || state.status}` : "";
  const approve = el("button", { type: "button", class: "btn", text: "Approve and build the assembly", disabled: state.status === "building" || !data.solidworks?.ready,
    onclick: () => post("/api/parts/assemble-approve", { attempt: asm.id }, "Starting the assembly build") });
  return el("div", { class: "asm-box" }, head,
    el("p", { class: "hint", text: `Assembly frame: ${asm.frame}` }),
    el("div", { class: "asm-table-wrap" }, el("table", { class: "asm-table" }, el("thead", {}, el("tr", {}, ["Part", "Copy", "Position (mm)", "Orientation", "From the drawing"].map((t) => el("th", { text: t })))), el("tbody", {}, rows))),
    (asm.problems || []).length ? el("ul", { class: "asm-notes" }, asm.problems.map((t) => el("li", { text: t }))) : null,
    (asm.left_out || []).length ? el("p", { class: "hint", text: `Left out (not built): ${asm.left_out.map((t) => t.split(":")[0]).join(", ")}` }) : null,
    plan.expected?.size ? el("p", { class: "hint", text: `Expected overall size: ${plan.expected.size.join(" × ")} mm (${plan.expected.size_evidence})` }) : null,
    el("div", { class: "part-actions" }, approve, prepare, status ? el("small", { class: "part-why", text: status }) : null),
    state.detail ? el("p", { class: "part-why-line", text: state.detail }) : null,
    asm.job ? el("button", { type: "button", class: "m-link", text: "Open the assembly job", onclick: () => { r.workflowJob = asm.job; window.dispatchEvent(new CustomEvent("studio:job", { detail: { job: asm.job } })); show(r); pollJob(r); } }) : null);
}

// ---- Parts on this sheet: a build list per part, then the existing approval / build / review / export path
const PART_STATUS = { ready: ["ok", "Build list ready"], needs_information: ["warn", "Needs information"], invalid: ["warn", "Build list not valid"], stopped: ["warn", "Not buildable yet"] };

function partsPanel(r) {
  const host = el("section", { class: "parts-panel", "aria-label": "Parts on this sheet" });
  let timer = null;
  const load = async (quiet) => {
    if (!quiet) host.replaceChildren(el("p", { class: "hint", text: "Finding the parts on this sheet…" }));
    try {
      const data = await (await fetch(`/api/parts/${r.id}`)).json();
      render(data);
      clearTimeout(timer);
      if ((["preparing", "resolving", "building", "assembling"].includes(data.batch?.phase) || data.batch?.assembly?.status === "building") && state.result === r) timer = setTimeout(() => load(true), 2500);   // live progress
    }
    catch (error) { host.replaceChildren(el("p", { class: "hint", text: `Could not list the parts: ${error.message}` })); }
  };
  const busy = (title, message) => window.dispatchEvent(new CustomEvent("studio:processing", { detail: { busy: Boolean(title), title, message } }));
  const post = async (path, body, title) => {
    busy(title, "One model call that sees only this part's views and your reviewed measurements. Nothing is built yet.");
    try { await workflowRequest(path, { result_id: r.id, llm: ($("#llm") && $("#llm").value) || r.llm, ...body }); await load(); }
    catch (error) { status.textContent = error.message; }
    finally { busy(null); }
  };
  const status = el("p", { class: "hint", "aria-live": "polite" });
  const render = (data) => {
    const sw = data.solidworks || {};
    const swLine = el("div", { class: `sw-status ${sw.ready ? "ok" : "warn"}` },
      el("b", { text: sw.ready ? `SolidWorks ready${sw.version ? " · " + sw.version : ""}` : sw.installed ? `${sw.version || "SolidWorks"}: not ready` : "SolidWorks not found" }),
      el("span", { text: sw.detail || "" }),
      el("button", { type: "button", class: "m-link", text: "Check again", onclick: load }));
    const rows = (data.parts || []).map((part) => {
      const list = part.list, check = list?.check || {};
      const [tone, label] = list ? PART_STATUS[check.status] || ["warn", check.status] : ["muted", "No build list yet"];
      const head = el("div", { class: "part-head" },
        el("div", {}, el("b", { class: "part-name", text: part.name }),
          el("small", { text: [part.item && `item ${part.item}`, part.quantity && `qty ${part.quantity}`, `${part.views.length} view${part.views.length === 1 ? "" : "s"}`].filter(Boolean).join(" · ") })),
        el("span", { class: `part-chip ${tone}`, text: label }));
      const body = el("div", { class: "part-body" });
      const file = el("input", { type: "file", accept: ".json,application/json", hidden: true });
      file.addEventListener("change", async () => {
        const f = file.files && file.files[0]; if (!f) return;
        try { await post("/api/parts/upload", { part_id: part.id, list: JSON.parse(await f.text()) }, `Loading the build list for ${part.name}`); }
        catch (error) { status.textContent = `That file is not a build list: ${error.message}`; }
      });
      const create = el("button", { type: "button", class: list ? "m-link" : "btn secondary", text: list ? "Create again with AI" : "Create build list with AI",
        onclick: () => post("/api/parts/create", { part_id: part.id }, `Writing the build list for ${part.name}`) });
      const upload = el("button", { type: "button", class: "m-link", text: list ? "Load another file" : "Load build list file", onclick: () => file.click() });
      if (list) {
        const features = (list.draft.items || []).map((it) => `${it.id} ${it.feature}${it.printed ? " · " + it.printed : ""}`);
        body.append(el("p", { class: "part-summary", text: `${(list.draft.items || []).length} feature${(list.draft.items || []).length === 1 ? "" : "s"} · from ${list.source === "ai" ? "the AI" : "a file"} · ${new Date(list.created).toLocaleString()}` }),
          el("details", { class: "part-features" }, el("summary", { text: "Features in this build list" }), el("ol", {}, features.map((t) => el("li", { text: t })))));
        if (check.status !== "ready") body.append(el("p", { class: "part-why-line", text: `${blockedReasons(part).join(" ")} Resolve it under “parts with doubts” above.` }));
        const gaps = (check.unsupported || []).map((u) => ({ u, box: el("input", { type: "checkbox", "aria-label": `Accept ${u.id}` }) }));
        if (gaps.length) body.append(el("div", { class: "part-gaps" }, el("b", { text: "Not modelled: tick each one you accept" }),
          gaps.map(({ u, box }) => el("label", {}, box, ` ${u.id}: ${u.text}`))));
        if (check.status === "ready") {
          const build = el("button", { type: "button", class: "btn", text: "Approve and build in SolidWorks" });
          const why = el("small", { class: "part-why" });
          const sync = () => {
            const missing = gaps.filter((g) => !g.box.checked).length;
            build.disabled = !sw.ready || missing > 0 || Boolean(r.workflowBusy);
            why.textContent = !sw.ready ? "SolidWorks is not ready on this machine: start it with a valid licence, then press Check again." :
              missing ? `Tick the ${missing} not-modelled item${missing === 1 ? "" : "s"} above to accept ${missing === 1 ? "it" : "them"}.` : "";
          };
          gaps.forEach((g) => g.box.addEventListener("change", sync)); sync();
          build.addEventListener("click", async () => {
            build.disabled = true;
            try {
              r.workflowJob = await workflowRequest("/api/workflow/start", { result_id: r.id, attempt: list.id });
              await jobDecision(r, "build", { accepted_gaps: gaps.map((g) => g.u.id) });
              if (typeof window !== "undefined") window.studioNavigate?.(3);
            } catch (error) { status.textContent = error.message; sync(); }
          });
          body.append(el("div", { class: "part-actions" }, build, why));
        }
      }
      if (!list) body.append(el("p", { class: "part-why-line", text: blockedReasons(part)[0] }));
      body.append(el("div", { class: "part-actions" }, create, upload, file));
      return el("article", { class: "part-card", "data-part": part.id }, head, body);
    });
    host.replaceChildren(el("div", { class: "parts-head" }, el("h3", { text: "Build in SolidWorks" })),
      flowBox(r, data, () => load(true), status), swLine, status,
      el("details", { class: "part-details" }, el("summary", { text: `Per-part details (${rows.length} part${rows.length === 1 ? "" : "s"})` }),
        el("div", { class: "part-list" }, rows)));
  };
  host.load = load;
  return host;
}


async function workflowRequest(path, body) {
  const response = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || response.statusText);
  return result;
}

function disposeViewers(host) {
  host.querySelectorAll("canvas").forEach((canvas) => {
    canvas.reviewObserver?.disconnect();
    canvas.reviewViewer?.dispose?.();
  });
}

function replaceWorkflow(r) {
  const host = $("#workflow-job");
  if (!host) return;
  disposeViewers(host);
  host.replaceChildren(workflowCard(r));
}

async function startBuild(r, attempt) {
  r.workflowJob = await workflowRequest("/api/workflow/start", {result_id:r.id, attempt});
  await jobDecision(r, "build");
}

async function jobDecision(r, action, decision = {}) {
  const job = r.workflowJob;
  r.workflowBusy = true;
  if (["build","approve_fix","edit_fix","retry_llm"].includes(action)) job.phase = "building";
  if (action === "export") job.phase = "exporting";
  window.dispatchEvent(new CustomEvent("studio:processing", {detail:{busy:true,title:action === "export" ? "Preparing your exports" : "SolidWorks is building your model",message:action === "export" ? "Checking the confirmed model and preparing files…" : "The CAD agent is executing the approved plan in SolidWorks…"}}));
  const body = {job_id:job.id, revision:job.revision, action, review_hash:job.review_hash, ...decision};
  show(r); pollJob(r);
  try {
    r.workflowJob = await workflowRequest("/api/workflow/decision", body);
    window.dispatchEvent(new CustomEvent("studio:job", {detail:{job:r.workflowJob}}));
  } catch (error) {
    const response = await fetch(`/api/workflow/${job.id}`);
    if (response.ok) r.workflowJob = await response.json();
    r.workflowError = error.message;
    window.dispatchEvent(new CustomEvent("studio:error", {detail:{message:error.message}}));
  } finally {
    r.workflowBusy = false;
    window.dispatchEvent(new CustomEvent("studio:processing", {detail:{busy:false}}));
    if (state.result === r) show(r);
  }
}

function pollJob(r) {
  if (r.pollTimer) return;
  const tick = async () => {
    r.pollTimer = null;
    if (state.result !== r || !r.workflowJob) return;
    try {
      const response = await fetch(`/api/workflow/${r.workflowJob.id}`);
      if (response.ok) {
        const job = await response.json();
        // A request may be waiting for its first durable write. Don't replace
        // its visible progress with the previous approval state.
        if (!r.workflowBusy || ["building","resuming","exporting"].includes(job.phase)) {
          const changed = JSON.stringify(r.workflowJob) !== JSON.stringify(job);
          r.workflowJob = job;
          window.dispatchEvent(new CustomEvent("studio:job", {detail:{job}}));
          const panel = $("#workflow-job");
          if (panel && changed) replaceWorkflow(r);
          if (changed) renderAgentChat(r);
        }
      }
    } catch (_) { /* keep the pending operation visible; the next poll retries */ }
    if (r.workflowBusy || ["building","resuming","exporting"].includes(r.workflowJob.phase)) r.pollTimer = setTimeout(tick, 2000);
  };
  if (r.workflowBusy || ["building","resuming","exporting"].includes(r.workflowJob.phase)) r.pollTimer = setTimeout(tick, 1500);
}

function workflowCard(r) {
  const job = r.workflowJob;
  const card = el("section", {class:"card pad", "aria-live":"polite"}, el("h2", {text:"SolidWorks and final review"}));
  if (!job) { card.append(el("p", {text:"Nothing is building yet. Resolve the open questions and approve the feature plan on the Review step to start SolidWorks."})); return card; }
  const labels = {awaiting_approval:"Ready for your approval", building:"Building in SolidWorks…", resuming:"Continuing…", exporting:"Preparing confirmed exports…",
    awaiting_fix_approval:"SolidWorks needs your confirmation", awaiting_review:"Compare your drawing with the 3D model", accepted:"Model confirmed — ready to export", exported:"Exports ready", needs_human:"Paused for your input", changes_requested:"Corrections requested", llm_failed:"Model call paused", aborted:"Job stopped"};
  card.append(el("p", {class:"workflow-status", text:labels[job.phase] || job.phase}));
  if (r.workflowError) card.append(el("p", {class:"msg err", text:r.workflowError}));
  if (job.stale || r.reviewDirty) {
    card.append(el("p", {class:"msg info", text:"The measurements or plan changed. Continue from the extraction review and build a new preview before confirming or exporting."}));
    return card;
  }
  if (["building","resuming","exporting"].includes(job.phase)) card.append(el("span", {class:"spinner"}), " The job is saved. You can reopen this result to see its status.");
  for (const question of job.questions || []) if (question) card.append(el("p", {class:"question", text:question}));
  const action = (text, name, extra = () => ({}), secondary = false) => {
    const button = el("button", {class:`btn${secondary ? " secondary" : ""}`, type:"button", text});
    button.disabled = Boolean(r.workflowBusy);
    button.addEventListener("click", () => { r.workflowError = ""; return jobDecision(r, name, extra()); });
    return button;
  };
  if (job.phase === "awaiting_approval") card.append(action("Approve features and build", "build"));
  if (job.phase === "awaiting_fix_approval" && job.proposal) {
    card.append(el("p", {text:job.proposal.reason}), el("pre", {text:JSON.stringify(job.proposal.diff, null, 2)}));
    const corrected = JSON.parse(JSON.stringify(job.proposal.params));
    const fields = el("fieldset", {class:"design-controls"});
    const inputs = [];
    const parameterFields = (value, path = []) => {
      for (const [key, item] of Object.entries(value)) {
        const keys = [...path, key];
        if (item && typeof item === "object") { parameterFields(item, keys); continue; }
        const input = el("input", {type:typeof item === "boolean" ? "checkbox" : "text", value:typeof item === "boolean" ? null : String(item), checked:item === true,
          "aria-label":`Correct ${keys.join(" ")}`});
        fields.append(el("label", {class:"row"}, keys.join(" · ").replaceAll("_", " ") + " ", input));
        inputs.push({keys, input, type:typeof item});
      }
    };
    parameterFields(corrected);
    const apply = el("button", {class:"btn", type:"button", text:"Apply my correction and continue"});
    const error = el("p", {class:"msg err"});
    apply.addEventListener("click", () => {
      try {
        for (const {keys,input,type} of inputs) {
          let parent = corrected;
          for (const key of keys.slice(0,-1)) parent = parent[key];
          const value = type === "number" ? Number(input.value) : type === "boolean" ? input.checked : input.value;
          if (type === "number" && (!input.value.trim() || !Number.isFinite(value))) throw new Error("Each measurement must be a finite number");
          parent[keys[keys.length-1]] = value;
        }
        return jobDecision(r, "edit_fix", {params:corrected});
      }
      catch (ex) { error.textContent = `Check the corrected values: ${ex.message}`; }
    });
    card.append(el("details", {}, el("summary", {text:"Correct the proposed feature values"}), fields, apply, error),
      el("div", {class:"actions"}, action("Confirm proposed correction and continue", "approve_fix"), action("Ask for a different correction", "reject_fix", () => ({message:"The proposed correction does not match the source drawing"}), true)));
  }
  if (job.preview) {
    const source = el("img", {src:`/api/page/${r.pages[0].sha}`, loading:"lazy", decoding:"async", alt:"Original 2D source drawing"});
    const pages = el("select", {"aria-label":"Compare drawing page"}, r.pages.map((p) => el("option", {value:p.sha, text:`Page ${p.no} · ${p.source}`})));
    pages.addEventListener("change", () => source.setAttribute("src", `/api/page/${pages.value}`));
    const canvas = el("canvas", {"aria-label":"Interactive 3D model. Drag to rotate and scroll to zoom.", tabindex:0});
    const preview = el("img", {src:job.preview.image, loading:"lazy", decoding:"async", alt:"SolidWorks model preview"});
    const viewerStatus = el("p", {class:"hint", text:"Drag to rotate · scroll to zoom. Inspect detailed dimensions in SolidWorks as well."});
    const views = el("div", {class:"actions"});
    card.append(el("div", {class:"compare"}, el("div", {}, el("h3", {text:"Your 2D drawing"}), pages, source),
      el("div", {}, el("h3", {text:"Created 3D model"}), canvas, views, viewerStatus, el("details", {}, el("summary", {text:"Saved preview image"}), preview))));
    if (typeof globalThis.renderReviewMesh === "function") {
      const startViewer = () => globalThis.renderReviewMesh(canvas, job.preview.mesh).then((viewer) => {
        if (!canvas.isConnected) { viewer.dispose?.(); return; }
        canvas.reviewViewer = viewer;
        for (const name of ["Isometric","Front","Top","Right"]) {
          const button = el("button", {class:"btn secondary", text:name});
          button.addEventListener("click", () => viewer.view(name)); views.append(button);
        }
      }).catch((error) => { canvas.hidden = true; viewerStatus.textContent = error.message; preview.parentNode.open = true; });
      if (typeof IntersectionObserver === "function") {
        canvas.reviewObserver = new IntersectionObserver((entries, observer) => {
          if (entries.some((entry) => entry.isIntersecting)) { observer.disconnect(); startViewer(); }
        });
        canvas.reviewObserver.observe(canvas);
      } else setTimeout(startViewer, 0);
    }
  }
  if (job.phase === "awaiting_review") {
    const compared = el("input", {type:"checkbox", "aria-label":"I compared the 3D model with the 2D drawing"});
    const measured = el("input", {type:"checkbox", "aria-label":"I verified the measurements and features"});
    const accept = action("Confirm model", "accept", () => ({compared_drawing:compared.checked, checked_measurements:measured.checked}));
    accept.disabled = true;
    const ready = () => { accept.disabled = !compared.checked || !measured.checked || Boolean(r.workflowBusy); };
    compared.addEventListener("change", ready); measured.addEventListener("change", ready);
    const correction = el("textarea", {"aria-label":"Correction after model review", placeholder:"Describe any measurement or feature that differs from the drawing", style:"width:100%"});
    card.append(el("label", {class:"row"}, compared, " I compared the 3D model with the 2D drawing"),
      el("label", {class:"row"}, measured, " I verified the measurements and features"),
      el("div", {class:"actions"}, accept), correction,
      action("Request correction", "correct", () => ({message:correction.value}), true));
  }
  if (["needs_human","changes_requested"].includes(job.phase)) {
    const back = el("button", {class:"btn secondary", type:"button", text:"Return to measurement review"});
    back.addEventListener("click", () => $("#design")?.scrollIntoView({behavior:"smooth"}));
    card.append(el("p", {text:"Correct the source measurements or add the requested clarification above, then continue to generate a revised plan. Any revised model needs another final review."}),
      back);
  }
  if (job.phase === "llm_failed") card.append(action("Retry model call", "retry_llm"));
  if (job.phase === "accepted") card.append(action("Export confirmed model", "export"));
  if (job.phase === "exported") card.append(el("div", {class:"actions"}, (job.exports || []).map((f) => el("a", {class:"btn", href:f.url, download:`model.${f.format === "BUNDLE" ? "zip" : f.format.toLowerCase()}`, text:f.format === "BUNDLE" ? "Download assembly with components" : `Download ${f.format}`}))));
  return card;
}

function assemblyCard(r) {
  const card = el("section", {class:"card pad", id:"assembly-review"}, el("h2", {text:"Assembly components and questions"}));
  const status = el("p", {class:"hint", "aria-live":"polite"});
  if (!r.assembly) {
    card.append(el("p", {text:r.assembly_extraction_error || "This drawing contains multiple components. Extract the component list and BOM in this workflow."}));
    const read = el("button", {class:"btn", text:"Continue assembly extraction"});
    read.addEventListener("click", async () => {
      read.disabled = true; status.textContent = "The extraction agent is reading the component list across the uploaded sheets…";
      window.dispatchEvent(new CustomEvent("studio:processing", {detail:{busy:true,title:"Extracting assembly components",message:"Reading component labels, quantities and assembly evidence…"}}));
      try { r.assembly = await workflowRequest("/api/assembly/read", {result_id:r.id, llm:r.llm}); show(r); }
      catch (error) { status.textContent = error.message; read.disabled = false; }
      finally { window.dispatchEvent(new CustomEvent("studio:processing", {detail:{busy:false}})); }
    });
    card.append(read, status); return card;
  }
  const artifact = r.assembly, plan = JSON.parse(JSON.stringify(artifact.plan));
  const controls = el("fieldset", {class:"design-controls"});
  const fields = [];
  let dirty = false;
  const edited = () => { dirty = true; r.reviewDirty = true; build.disabled = true; if ($("#workflow-job")) replaceWorkflow(r); renderAgentChat(r); };
  const input = (label, value, set, type = "text") => {
    const field = el("input", {type, value:type === "checkbox" ? null : value ?? "", checked:type === "checkbox" && value === true, "aria-label":label});
    fields.push(() => set(type === "checkbox" ? field.checked : field.value));
    return el("label", {class:"row"}, label + " ", field);
  };
  const select = (label, value, options, set) => {
    const field = el("select", {"aria-label":label}, options.map(([key,text]) => el("option", {value:key,text})));
    field.value = value ?? ""; fields.push(() => set(field.value)); return el("label", {class:"row"}, label + " ", field);
  };
  const unit = select("Assembly unit", plan.unit, [["","Confirm unit"],["mm","Millimetres"],["inch","Inches"]], (v) => {plan.unit=v || null;});
  controls.append(unit, input("I verified the units", plan.unit_confirmed, (v) => {plan.unit_confirmed=v;}, "checkbox"),
    input("The component list is complete", plan.bom_complete, (v) => {plan.bom_complete=v;}, "checkbox"));
  const notes = [];
  for (const [index, question] of (plan.issues || []).entries()) {
    const answer = el("input", {type:"text", "aria-label":`Assembly answer ${index+1}`, placeholder:"Source correction / evidence resolving this question"});
    const resolved = el("input", {type:"checkbox", "aria-label":`Resolve assembly question ${index+1}`});
    notes.push({question, answer, resolved});
    controls.append(el("div", {class:"question"}, el("p", {text:question}), answer, el("label", {class:"row"}, resolved, " I resolved this against the source")));
  }
  for (const row of plan.bom) {
    const section = el("details", {open:true}, el("summary", {text:`${row.id} · ${row.part_number || row.description || "Component needs identification"}`}));
    section.append(input(`${row.id} Part number`, row.part_number, (v) => {row.part_number=v.trim();}),
      input(`${row.id} Description`, row.description, (v) => {row.description=v.trim();}),
      input(`${row.id} Quantity`, row.quantity, (v) => {row.quantity=v.trim() ? Number(v) : null;}),
      select(`${row.id} Component type`, row.kind, [["unknown","Needs identification"],["part","Leaf part"],["assembly","Subassembly (needs leaf parts)"]], (v) => {row.kind=v;}),
      input(`${row.id} Source scope`, row.scope, (v) => {row.scope=v;}),
      input(`${row.id} I verified identity and quantity`, row.confirmed, (v) => {row.confirmed=v;}, "checkbox"),
      el("p", {class:"hint", text:row.evidence.map((e) => `Page ${e.page}: ${e.text}`).join("; ")}));
    if (row.issues.length) {
      const answer = el("input", {type:"text", "aria-label":`Resolve ${row.id} conflicts`, placeholder:"Explain the correct identity/quantity from the source"});
      section.append(el("p", {class:"question", text:row.issues.join("; ")}), answer);
      fields.push(() => {if (row.confirmed && answer.value.trim()) {row.evidence.push({page:row.evidence[0]?.page || 1,text:`Human correction: ${answer.value.trim()}`}); row.issues=[];}});
    }
    const model = plan.models.find((m) => m.bom_id === row.id);
    if (model) section.append(el("p", {text:`Model: ${model.source_name} · ${model.configuration}`}),
      input(`${row.id} I verified this component model`, model.confirmed, (v) => {model.confirmed=v;}, "checkbox"));
    const file = el("input", {type:"file", accept:".sldprt", "aria-label":`${row.id} Component model`});
    const config = el("input", {type:"text", "aria-label":`${row.id} Model configuration`, placeholder:"Exact configuration name"});
    const evidence = el("input", {type:"text", "aria-label":`${row.id} Model evidence`, placeholder:"Reviewed component drawing / revision"});
    const attach = el("button", {class:"btn secondary", type:"button", text:`Attach model for ${row.id}`});
    attach.addEventListener("click", async () => {
      try {
        if (!file.files[0] || !config.value.trim() || !evidence.value.trim()) throw new Error("Choose the component file, configuration and source evidence");
        if (file.files[0].size > 40*1024*1024) throw new Error("Component file must be under 40 MB");
        attach.disabled = true;
        const uploaded = await workflowRequest("/api/assembly/model", {name:file.files[0].name, data:await toBase64(file.files[0])});
        plan.models=plan.models.filter((m) => m.bom_id !== row.id);
        plan.models.push({bom_id:row.id, sha256:uploaded.sha256, source_name:uploaded.source_name, configuration:config.value.trim(), origin:"supplied", provenance:evidence.value.trim(), confirmed:false});
        fields.forEach((save) => save());
        r.assembly=await workflowRequest("/api/assembly/review", {result_id:r.id, attempt:artifact.id, plan});
        r.reviewDirty=false; if(r.workflowJob)r.workflowJob.stale=true; show(r);
      } catch (error) {status.textContent=error.message; attach.disabled=false;}
    });
    section.append(el("p", {class:"hint", text:"If the drawing does not fully define this component, attach its reviewed native model here."}), file, config, evidence, attach);
    controls.append(section);
  }
  controls.append(el("h3", {text:"Component placements"}), el("p", {class:"hint", text:"Enter drawing-backed positions and orientation. Missing placements remain questions; the agent does not guess them."}));
  // New occurrences are placeholders until the user supplies and confirms data.
  for (const row of plan.bom) {
    if (!Number.isInteger(row.quantity) || row.quantity < 1 || row.quantity > 1000) continue;
    const count = plan.occurrences.filter((o) => o.bom_id === row.id).length;
    for (let i=count;i<row.quantity;i++) {
      let id=`${row.id}-${i+1}`; while(plan.occurrences.some((o) => o.id===id))id+="x";
      plan.occurrences.push({id,bom_id:row.id,placement:null,fixed:false});
    }
  }
  for (const occurrence of plan.occurrences) {
    const placement = occurrence.placement;
    const coordinates = [0,1,2].map((i) => el("input", {type:"text",value:placement?.translation[i] ?? "", "aria-label":`${occurrence.id} ${["X","Y","Z"][i]}`}));
    const rotation = el("input", {type:"text",value:placement?.rotation.join(", ") || "", "aria-label":`${occurrence.id} Rotation`,placeholder:"9 rotation matrix values"});
    const evidence = el("input", {type:"text",value:placement?.evidence || "", "aria-label":`${occurrence.id} Placement evidence`,placeholder:"Dimensions / datums defining this placement"});
    const confirmed = el("input", {type:"checkbox",checked:placement?.confirmed, "aria-label":`${occurrence.id} Confirm placement`});
    const aligned = el("button", {type:"button", class:"btn secondary", text:"Use aligned axes"});
    aligned.addEventListener("click", () => {rotation.value="1,0,0,0,1,0,0,0,1";edited();});
    fields.push(() => {
      if (coordinates.every((c) => c.value.trim()) && rotation.value.trim() && evidence.value.trim()) occurrence.placement={translation:coordinates.map((c) => Number(c.value)),rotation:rotation.value.split(",").map(Number),evidence:evidence.value.trim(),confirmed:confirmed.checked};
      else occurrence.placement=null;
    });
    controls.append(el("details", {}, el("summary", {text:`${occurrence.id} · ${occurrence.bom_id}`}),
      el("div", {class:"actions"}, ...coordinates), rotation, aligned, evidence,
      el("label", {class:"row"}, confirmed," I verified this placement"), input(`${occurrence.id} Fixed reference`, occurrence.fixed, (v) => {occurrence.fixed=v;}, "checkbox")));
  }
  controls.append(el("h3", {text:"Final dimensional checks"}));
  const size = [0,1,2].map((i) => el("input", {type:"text",value:plan.expected.size?.[i] ?? "", "aria-label":`Assembly overall ${["X","Y","Z"][i]}`,placeholder:`Overall ${["X","Y","Z"][i]}`}));
  fields.push(() => {plan.expected.size=size.every((s) => s.value.trim()) ? size.map((s) => Number(s.value)) : null;});
  controls.append(el("div", {class:"actions"}, ...size), input("Overall dimensions evidence",plan.expected.size_evidence,(v) => {plan.expected.size_evidence=v;}));
  const mates = el("textarea", {"aria-label":"Assembly connections",style:"min-height:180px"}); mates.value=JSON.stringify(plan.mates,null,2);
  fields.push(() => {plan.mates=JSON.parse(mates.value);});
  controls.append(el("details", {}, el("summary", {text:"Assembly connections and mate references"}), el("p", {class:"hint",text:"Specify reviewed mate surfaces and constraints here. Unresolved connections stay paused for clarification."}), mates));
  const blockers = el("ul", {}, artifact.blockers.map((q) => el("li", {text:q})));
  const save = el("button", {class:"btn",type:"button",text:"Save assembly review and check questions"});
  const build = el("button", {class:"btn",type:"button",text:"Approve assembly and build in SolidWorks"});
  build.disabled=artifact.status !== "ready_for_approval" || Boolean(r.drawing_model?.issues.length) || Boolean(r.workflowBusy);
  save.addEventListener("click", async () => {
    try {
      fields.forEach((commit) => commit());
      if(notes.some((q) => q.resolved.checked && !q.answer.value.trim()))throw new Error("Give the source correction for each question you mark resolved");
      plan.issues=notes.filter((q) => !q.resolved.checked).map((q) => q.question);
      const review_notes=notes.filter((q) => q.resolved.checked).map((q) => `${q.question}: ${q.answer.value.trim()}`);
      controls.disabled=true; status.textContent="Saving your corrections and checking remaining questions…";
      r.assembly=await workflowRequest("/api/assembly/review", {result_id:r.id,attempt:artifact.id,plan,review_notes});
      r.reviewDirty=false;if(r.workflowJob)r.workflowJob.stale=true;show(r);
    } catch(error){status.textContent=error.message;controls.disabled=false;}
  });
  build.addEventListener("click", async () => {if(!dirty){build.disabled=true;try{await startBuild(r,artifact.id);}catch(error){status.textContent=error.message;build.disabled=false;}}});
  controls.addEventListener("input",edited);controls.addEventListener("change",edited);
  controls.append(el("div", {class:"actions"},save,build));
  card.append(el("p", {text:"The extraction agent identified this as a component or assembly sheet. Resolve the readings, quantities, component geometry and assembly questions here; the final model uses the same verification and export steps."}),controls,blockers,status);
  return card;
}

