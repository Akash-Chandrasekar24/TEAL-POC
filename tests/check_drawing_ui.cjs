/* Dependency-free DOM smoke test for the evidence-review card.
 * node tests/check_drawing_ui.cjs <fixture.json>
 * This checks event/data behavior, not browser layout or rendering.
 */
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");

class Element {
  constructor(tag) {
    this.tagName = tag; this.attrs = {}; this.children = []; this.listeners = {};
    this.value = ""; this.checked = false; this.disabled = false; this._text = "";
  }
  setAttribute(key, value) {
    this.attrs[key] = String(value);
    if (key === "value") this.value = String(value);
    if (key === "checked") this.checked = true;
  }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = []; this._text = ""; this.append(...nodes); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map((n) => typeof n === "string" ? n : n.textContent).join(""); }
  async fire(name) { for (const fn of this.listeners[name] || []) await fn({ currentTarget: this }); }
}
function all(node, predicate) {
  return [node, ...node.children.flatMap((n) => n instanceof Element ? all(n, () => true) : [])].filter(predicate);
}

async function main() {
  const fixture = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const html = fs.readFileSync("app/web/index.html", "utf8");
  const script = html.split("<script>")[1].split("</script>")[0].split("const drop = ")[0];
  const provider = new Element("select"); provider.value = "gemini";
  let request;
  const context = {
    document: { createElement: (tag) => new Element(tag), createElementNS: (_, tag) => new Element(tag),
      querySelector: (selector) => selector === "#llm" ? provider : null },
    fetch: async (path, options) => {
      assert.equal(path, "/api/plan"); request = JSON.parse(options.body);
      return { ok: true, json: async () => fixture.response };
    },
  };
  vm.createContext(context); vm.runInContext(script, context);
  const card = context.designCard(fixture.result);
  const build = () => all(card, (e) => e.tagName === "button" && e.textContent === "Approve features and build in SolidWorks");
  const download = () => all(card, (e) => e.tagName === "a" && e.attrs.download);
  assert.equal(build().length, 1);
  assert.equal(download().length, 0, "planning must not offer CAD exports");
  const correction = all(card, (e) => e.attrs["aria-label"] === "Correct P1.d1")[0];
  correction.value = "125"; await correction.fire("input");
  const confirmed = all(card, (e) => e.attrs["aria-label"] === "Confirm P1.d1")[0];
  assert.equal(confirmed.checked, true);
  const controls = all(card, (e) => e.tagName === "fieldset")[0];
  await controls.fire("input"); // browser input events bubble to the fieldset
  assert.equal(build().length, 0, "editing must hide the previous plan's build approval");
  const generate = all(card, (e) => e.tagName === "button" && e.textContent === "Review measurements and continue")[0];
  await generate.fire("click");
  assert.equal(request.source_hash, fixture.result.drawing_model.source_hash);
  assert.equal(request.result_id, fixture.result.id);
  assert.equal(request.resolutions.dimensions[0].text, "125");
  assert.equal(controls.disabled, false);
  assert.equal(build().length, 1);
  assert.equal(download().length, 0);
  assert.ok(card.textContent.includes("125"));
  assert.ok(card.textContent.includes("approval"));
  const reviewed = fixture.result;
  reviewed.workflowJob = {id:"abcdefabcdef", revision:2, phase:"awaiting_review", review_hash:"preview-1", questions:[], preview:{image:"/preview",mesh:"/mesh"}};
  reviewed.reviewDirty = false;
  const review = context.workflowCard(reviewed);
  assert.equal(all(review,(e) => e.attrs.download).length,0,"review must not expose exports");
  const confirm=all(review,(e) => e.tagName==="button" && e.textContent==="Confirm model")[0];
  assert.equal(confirm.disabled,true);
  const checks=all(review,(e) => e.tagName==="input" && e.attrs.type==="checkbox");
  checks[0].checked=true; await checks[0].fire("change"); assert.equal(confirm.disabled,true);
  checks[1].checked=true; await checks[1].fire("change"); assert.equal(confirm.disabled,false);
  reviewed.workflowJob.phase="accepted";
  const accepted=context.workflowCard(reviewed);
  assert.equal(all(accepted,(e) => e.tagName==="button" && e.textContent==="Export confirmed model").length,1);
  assert.equal(all(accepted,(e) => e.attrs.download).length,0);
  reviewed.workflowJob.phase="exported";reviewed.workflowJob.exports=[{format:"STEP",url:"/download/STEP"}];
  assert.equal(all(context.workflowCard(reviewed),(e) => e.attrs.download).length,1);
  reviewed.reviewDirty=true;
  assert.equal(all(context.workflowCard(reviewed),(e) => e.attrs.download).length,0,"unsaved measurement edits hide exports");
  const assemblyResult=JSON.parse(JSON.stringify(fixture.result));
  assemblyResult.assembly={id:"aaaaaaaaaaaa",result_id:assemblyResult.id,plan:fixture.assembly,status:"ready_for_approval",blockers:[]};
  assemblyResult.reviewDirty=false;
  assert.equal(context.isAssembly(assemblyResult),true);
  const assembly=context.assemblyCard(assemblyResult);
  assert.equal(all(assembly,(e) => e.attrs["aria-label"]==="B1 Quantity").length,1);
  assert.equal(all(assembly,(e) => e.attrs["aria-label"]==="B1 Component model").length,1);
  assert.equal(all(assembly,(e) => e.attrs["aria-label"]==="base X").length,1);
  assert.equal(all(assembly,(e) => e.tagName==="a").length,0,"assembly review stays on the same screen");
  const assemblyBuild=all(assembly,(e) => e.tagName==="button" && e.textContent==="Approve assembly and build in SolidWorks")[0];
  assert.equal(assemblyBuild.disabled,false);
  const assemblyFields=all(assembly,(e) => e.tagName==="fieldset")[0];
  await assemblyFields.fire("input");assert.equal(assemblyBuild.disabled,true,"assembly edits need a fresh review");
  const measurementReview=context.designCard(assemblyResult);
  assert.equal(all(measurementReview,(e) => e.tagName==="button" && e.textContent==="Save reviewed measurements").length,1,"assembly measurements remain editable inline");
  assert.ok(html.includes('id="upload-panel"') && html.includes('id="agent-chat"'),"upload and chat need distinct panels");
  const emptyChat=context.agentChatBody(null);
  assert.equal(all(emptyChat,(e) => e.attrs["aria-label"]==="Your reply")[0].disabled,true);
  const chatResult=JSON.parse(JSON.stringify(fixture.result));
  chatResult.reviewDirty=false;chatResult.workflowJob=null;chatResult.review_hash="review-1";
  chatResult.chatDraft={mode:"correct",target:"note",text:"",context:"",measurement:"",view:""};
  chatResult.chatHistory=[{target:"P1.d1",reply:"125",acknowledgement:"Saved the corrected measurement"}];
  const chat=context.agentChatBody(chatResult);
  assert.ok(chat.textContent.includes("Saved the corrected measurement"));
  const chatInput=all(chat,(e) => e.attrs["aria-label"]==="Your reply")[0];
  const applyReply=all(chat,(e) => e.tagName==="button" && e.textContent==="Apply reply to review")[0];
  assert.equal(applyReply.disabled,true);
  chatInput.value="125";await chatInput.fire("input");assert.equal(applyReply.disabled,false);
  chatResult.reviewDirty=true;await chatInput.fire("input");assert.equal(applyReply.disabled,true,"chat must preserve unsaved workspace edits");
  chatResult.chatDraft={mode:"ask",target:"P1.d1",text:"What does this reading mean?"};
  const questionChat=context.agentChatBody(chatResult);
  assert.equal(all(questionChat,e=>e.tagName==="button" && e.textContent==="Ask drawing assistant")[0].disabled,false,"read-only questions preserve unsaved edits");
  const confidenceResult=JSON.parse(JSON.stringify(fixture.result));
  confidenceResult.drawing_model.dimensions[0].confirmed=false;
  confidenceResult.drawing_model.dimensions[0].corrected_text=null;
  confidenceResult.measurement_review={"P1.d1":{confidence:96,confidence_reason:"Source text is clear",ai_approved:true},"P1.d2":{confidence:80,ai_approved:false,review_reason:"Agent confidence is 80% or lower"}};
  const confidenceCard=context.designCard(confidenceResult);
  const approvedLane=all(confidenceCard,e=>e.attrs["aria-label"]==="AI approved")[0];
  const reviewLane=all(confidenceCard,e=>e.attrs["aria-label"]==="Needs review")[0];
  assert.equal(all(approvedLane,e=>e.attrs["data-reading-id"]==="P1.d1").length,1);
  assert.equal(all(reviewLane,e=>e.attrs["data-reading-id"]==="P1.d2").length,1,"80 percent needs review");
  assert.ok(confidenceCard.textContent.includes("96%") && confidenceCard.textContent.includes("Not scored"));
  const showBody=html.slice(html.indexOf("function show(r)"),html.indexOf("function updateExtractionProgress"));
  assert.ok(!showBody.includes("assemblyCard(r)") && !showBody.includes("featureCard(r)") && !showBody.includes("assessmentCard(r)"),"review page omits the removed inspector sections");
  console.log("Drawing review UI smoke test passed");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
