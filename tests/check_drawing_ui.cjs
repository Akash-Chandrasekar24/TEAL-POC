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
  const script = fs.readFileSync("app/web/solidworks.js", "utf8") + ";\n" + html.split("<script>")[1].split("</script>")[0].split("const drop = ")[0].replace('"use strict";', "");
  const provider = new Element("select"); provider.value = "gemini";
  let request;
  const context = {
    document: { createElement: (tag) => new Element(tag), createElementNS: (_, tag) => new Element(tag),
      querySelector: (selector) => selector === "#llm" ? provider : null },
    fetch: async (path, options) => {
      assert.equal(path, "/api/review", "the review page only saves the review: no planning, no build"); request = JSON.parse(options.body);
      return { ok: true, json: async () => fixture.response };
    },
  };
  vm.createContext(context); vm.runInContext(script, context);
  const card = context.designCard(fixture.result);
  const build = () => all(card, (e) => e.tagName === "button" && e.textContent === "Approve features and build in SolidWorks");
  const download = () => all(card, (e) => e.tagName === "a" && e.attrs.download);
  assert.equal(build().length, 0, "building lives on the SolidWorks page, not in the review");
  assert.equal(download().length, 0, "the review must not offer CAD exports");
  const correction = all(card, (e) => e.tagName === "input" && e.attrs["aria-label"] === "Correct P1.d1")[0];
  correction.value = "125";
  const save = all(card, (e) => e.attrs["aria-label"] === "Save value P1.d1")[0];
  await save.fire("click");                                   // saving a corrected value also marks it verified
  assert.equal(all(card, (e) => e.attrs["data-reading-id"] === "P1.d1").length, 0, "a verified reading leaves the Needs review tab");
  const controls = all(card, (e) => e.tagName === "fieldset")[0];
  await controls.fire("input"); // browser input events bubble to the fieldset
  const generate = all(card, (e) => e.tagName === "button" && e.textContent === "Save reviewed measurements")[0];
  await generate.fire("click");
  assert.equal(request.source_hash, fixture.result.drawing_model.source_hash);
  assert.equal(request.result_id, fixture.result.id);
  assert.equal(request.resolutions.dimensions[0].text, "125");
  assert.equal(controls.disabled, false);
  assert.equal(build().length, 0);
  assert.equal(download().length, 0);
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
  const ids=(node) => all(node,(e) => e.attrs["data-reading-id"]).map((e) => e.attrs["data-reading-id"]);
  assert.ok(ids(confidenceCard).includes("P1.d2") && !ids(confidenceCard).includes("P1.d1"),"80 percent needs review; 96 percent is approved");
  assert.ok(confidenceCard.textContent.includes("Not scored"));
  assert.ok(confidenceCard.textContent.includes("To verify") && confidenceCard.textContent.includes("AI verified"));
  const aiTab=all(confidenceCard,(e) => e.attrs["data-tab"]==="ai")[0];
  await aiTab.fire("click");
  assert.deepEqual(ids(confidenceCard),["P1.d1"],"the AI approved tab lists only approved readings");
  assert.ok(confidenceCard.textContent.includes("96%"));
  const allTab=all(confidenceCard,(e) => e.attrs["data-tab"]==="all")[0];
  await allTab.fire("click");
  assert.equal(ids(confidenceCard).length,confidenceResult.drawing_model.dimensions.length,"All lists every reading");
  const search=all(confidenceCard,(e) => e.attrs["aria-label"]==="Search measurements")[0];
  search.value="P1.d2";await search.fire("input");
  assert.deepEqual(ids(confidenceCard),["P1.d2"],"search narrows the list");
  const where=context.measureBoxes({pages:[{no:1,width:860,height:609,boxes:[[0,0,10,10]],read:{dims:[{id:"P1.d1",text:"5",views:[1]}],evidence:{extraction:{callouts:[{category:"measurement",text:"5",box:[500,500,1000,1000]}]}}}}]});
  assert.deepEqual(JSON.parse(JSON.stringify(where["P1.d1"])),{page:1,box:[430,304.5,860,609],exact:true},"a callout box (normalized 0..1000) is converted to page pixels and wins over the view box");
  const legacy=context.measureBoxes({pages:[{no:1,boxes:[[0,0,10,10]],read:{dims:[{id:"P1.d1",text:"5",views:[1]}]}}]});
  assert.deepEqual(JSON.parse(JSON.stringify(legacy["P1.d1"])),{page:1,box:[0,0,10,10],exact:false},"without a callout box the view box is used");
  const ev=(kind,tb,notes,value,resolutions={})=>JSON.parse(JSON.stringify(context.settingEvidence(kind,{pages:[{title_block:tb,notes}],resolutions},value)));
  assert.equal(ev("projection",{projection_symbol:{status:"present",convention:"first",evidence:"cone left, circles right"}},[],"first").level,"high","a read symbol that agrees is strong evidence");
  assert.equal(ev("projection",{projection_symbol:{status:"present",convention:"first"}},[],"third").level,"check","the sheet's symbol disagrees with the choice");
  assert.equal(ev("projection",{projection:"third"},[],"third").level,"medium","title block text only");
  assert.equal(ev("projection",{},[],"third").level,"low","no sheet evidence");
  assert.equal(ev("projection",{},[],"third",{projection:"third"}).level,"you","a value the reviewer set, with no support on the sheet");
  assert.equal(ev("projection",{projection_symbol:{status:"present",convention:"first"}},[],"first",{projection:"first"}).level,"high","sheet evidence still shows when the reviewer chose the same value");
  assert.equal(ev("unit",{unit:"mm"},["ALL DIMENSIONS IN MM"],"mm").level,"high","title block and printed note agree");
  assert.equal(ev("unit",{unit:"mm"},[],"mm").level,"medium");
  assert.equal(ev("unit",{},["Dimensions in inches"],"mm").level,"check","a printed note names a different unit");
  assert.ok(card.textContent.includes("Medium · title block"),"projection shows how well the sheet supports it");
  const groupResult=JSON.parse(JSON.stringify(fixture.result));
  groupResult.measurement_review={"P1.d1":{confidence:96,ai_approved:true},"P1.d2":{confidence:70,ai_approved:false,review_reason:"low"}};
  const grouped=context.designCard(groupResult);
  const heads=all(grouped,(e) => e.tagName==="summary" && e.attrs.class==="m-ghead");
  assert.ok(heads.length>=1,"readings are grouped by the drawing's views");
  assert.ok(heads.some((h) => h.textContent.includes("to verify")),"a group says how many readings still need verifying");
  assert.equal(JSON.parse(JSON.stringify(context.viewGroup({pages:[]},{views:[]}))).key,"none","a reading without a view gets its own group");
  const sec=context.viewGroup({pages:[{no:1,read:{views:[{no:2,type:"section",label:"SECTION A-A SCALE 1:1,5"}]}}]},{views:["P1.v2"]});
  assert.deepEqual(JSON.parse(JSON.stringify([sec.title,sec.scale,sec.kind,sec.key])),["SECTION A-A","scale 1:1,5","Section","1.2"],"sheet name, scale and kind are split out");
  assert.ok(html.includes('<script src="/solidworks.js"></script>') && !html.includes("function workflowCard") && !html.includes("function partsPanel"),"all SolidWorks UI code lives in solidworks.js");
  const csv=context.measurementsCsv(context.measurementRows([
    {group:{title:"SECTION A-A",scale:"scale 1:1,5"},d:{id:"P1.d1",text:"Ø79",page:1},shown:() => 'Ø79 "x"',state:() => "ai",score:87},
    {group:{title:"01. PISTON",scale:""},d:{id:"P1.d2",text:"5",page:1},shown:() => "5",state:() => "open",score:null}]));
  assert.ok(csv.startsWith("﻿view,id,value,original,status,confidence_percent,page"),"CSV starts with a BOM (Excel) and a header");
  assert.ok(csv.includes('"SECTION A-A (scale 1:1,5)","P1.d1","Ø79 ""x""","Ø79","AI verified","87","1"'),"cells are quoted and quotes doubled");
  assert.ok(csv.includes('"01. PISTON","P1.d2","5","5","To verify","",\"1\"'),"a missing score stays empty");
  const qr={pages:[{no:1,read:{issues:["Scale label","The rotated dimension reads 42"]}}]};
  assert.equal(context.clarificationKey(qr,"Page 1: unreadable source callout: The rotated dimension reads 42"),"P1.u2","an issue line maps back to its question id");
  assert.equal(context.clarificationKey(qr,"Confirm projection: unreadable"),null,"other blockers are not clarification questions");
  assert.equal(context.clarificationKey(qr,"Page 1: unreadable source callout: not a real question"),null,"an unknown question is not answerable");
  const qlist=context.el("ul",{},context.questionCards(qr,["Page 1: unreadable source callout: Scale label","Confirm projection: unreadable"]).map((c) => c.node));
  assert.equal(all(qlist,(e) => e.attrs["data-question"]==="P1.u1").length,1,"a clarification question gets an answer box");
  assert.equal(all(qlist,(e) => e.tagName==="textarea" && e.attrs["aria-label"]==="Answer P1.u1").length,1);
  assert.equal(all(qlist,(e) => e.tagName==="li").length,2,"a non-clarification blocker stays plain text");
  const twinR={pages:[{no:2,read:{issues:["The Ø20 callout on the coupling side section is visually crowded.","f8: The attachment of the Ø20 callout is crowded."]}}]};
  const twins=context.questionCards(twinR,twinR.pages[0].read.issues.map((q) => `Page 2: unreadable source callout: ${q}`));
  assert.equal(twins.length,1,"the same question raised twice is one card");
  assert.equal(JSON.stringify(twins[0].keys),JSON.stringify(["P2.u1","P2.u2"]),"and its answer goes to both");
  assert.ok(twins[0].node.textContent.includes("Also answers P2.u2"),"the card says so");
  assert.equal(context.suggestPanel(qr,["Confirm projection: unreadable"]),null,"no AI option when nothing can be answered here");
  const sp=context.suggestPanel(qr,["Page 1: unreadable source callout: Scale label"]);
  assert.ok(all(sp,(e) => e.tagName==="button" && e.textContent==="Let AI suggest answers (1)").length===1,"the AI option is offered");
  class Host extends Element { replaceChildren(...n) { this.children = n; } }
  const hostEl=new Host("div");
  const rs={id:"abcdef123456",pages:[{no:1,sha:"a".repeat(64),width:860,height:609,boxes:[[0,0,100,100],[100,0,300,200],[300,300,400,400]]}]};
  context.suggestionCards(rs,{suggestions:[{key:"P1.u1",page:1,question:"q",can_answer:true,answer:"It reads 22",measurement:"22",view:"P1.v2",basis:"label"},
                                           {key:"P1.u2",page:1,question:"q2",can_answer:false,answer:"",measurement:null,view:null,basis:"too small"}]},hostEl);
  assert.equal(all(hostEl,(e) => e.attrs["aria-label"]==="Accept P1.u1")[0].checked,false,"a draft that proposes changing a reading is never pre-ticked");
  assert.equal(all(hostEl,(e) => e.attrs["aria-label"]==="Also set the reading for P1.u1")[0].checked,false,"adding a reading is opt-in");
  assert.equal(all(hostEl,(e) => e.attrs["aria-label"]==="Accept P1.u2").length,0,"an unanswerable question has nothing to accept");
  const host2=new Host("div");
  context.suggestionCards(rs,{suggestions:[{key:"P1.u1",page:1,question:"a",can_answer:true,answer:"A label",measurement:null,view:null,basis:"b"},
                                           {key:"P1.u3",page:1,question:"c",can_answer:true,answer:"A scale",measurement:null,view:null,basis:"d"}]},host2);
  assert.ok(all(host2,(e) => e.attrs["aria-label"]==="Accept P1.u1")[0].checked && all(host2,(e) => e.attrs["aria-label"]==="Accept P1.u3")[0].checked,"plain answers are ticked for the reviewer");
  assert.ok(host2.textContent.includes("Confirm and save 2 answers") && host2.textContent.includes("Nothing is saved until you confirm"),"one confirmation saves every ticked answer");
  assert.ok(hostEl.textContent.includes("Is this OK?") && hostEl.textContent.includes("Nothing is saved until you confirm"),"the reviewer is asked before anything is saved");
  assert.ok(hostEl.textContent.includes("Check this reading against the view"),"a proposed reading carries a caution");
  assert.equal(all(hostEl,(e) => e.attrs.class==="ai-crop").length,1,"a draft shows the view it refers to");
  assert.equal(all(hostEl,(e) => e.tagName==="button" && e.textContent==="Confirm and save the accepted answers").length,1);
  const sum=context.summaryText([{accept:{checked:true},sg:{can_answer:true,measurement:null}},{accept:{checked:false},sg:{can_answer:true,measurement:"22"}}],[{can_answer:true},{can_answer:true},{can_answer:false}]);
  assert.equal(sum,"1 draft is ticked for you. 1 draft proposes a corrected reading and is left unticked for your own check. The AI could not tell 1 question. Nothing is saved until you confirm. Edit or untick anything first.","the summary reads correctly");
  const gated=context.designCard(confidenceResult);
  assert.ok(all(gated,(e) => e.tagName==="button" && /^Approve all measurements \(\d+\)$/.test(e.textContent)).length===1,"one button approves every reading");
  const sections=all(gated,(e) => (e.attrs.class||"").startsWith("m-navbtn") && !(e.attrs.class||"").includes("m-qnav"));
  assert.ok(sections.length>=1,"a button per section of the drawing");
  assert.ok(all(gated,(e) => e.attrs.class==="m-gapprove" && /^Approve section \(\d+\)$/.test(e.textContent)).length>=1,"each section is approved from its own header");
  assert.ok(!gated.textContent.includes("Measurements, made clear."),"plain heading");
  assert.ok(gated.textContent.includes("Open questions appear here once every measurement is approved"),"questions wait for the measurements");
  assert.equal(all(gated,(e) => e.attrs.id==="build-plan")[0].hidden,true,"the questions block is hidden until approval");
  assert.ok(html.includes('class="ai-fab"') && html.includes('class="chat-close"'),"the assistant opens from a floating button and closes from its window");
  const pageR={pages:[{no:1,sha:"a".repeat(64),width:1000,height:500,boxes:[[0,0,500,500],[500,0,1000,500]],read:{views:[{no:1,label:"FRONT"},{no:2,label:"SECTION D-D"}],
    evidence:{extraction:{callouts:[{id:"o19",text:"SCALE 1:1,5",box:[100,100,200,150]}],features:[{id:"f7",views:["v2"]}]}}}}]};
  const spot=(t) => JSON.parse(JSON.stringify(context.questionSpot(pageR,"P1.u2",t)));
  assert.deepEqual(spot("o19: conflict: SCALE 1:1,5").box,[100,50,200,75],"a callout id points at that callout (normalized box -> pixels)");
  assert.deepEqual(spot("Confirm whether the decimal comma in '1:1,5' is 1:1.5").box,[100,50,200,75],"quoted printed text finds its callout");
  assert.deepEqual(spot("v2: confirm the cutting plane").box,[500,0,1000,500],"a view id points at that view");
  assert.deepEqual(spot("f7: is 12 a hole depth?").box,[500,0,1000,500],"a feature points at its view");
  assert.deepEqual(spot("The cutting plane of SECTION D-D is not visible").box,[500,0,1000,500],"a section name finds its view");
  assert.equal(spot("No tolerance is printed").whole,true,"no spot named: the whole page");
  assert.equal(context.questionSpot(pageR,"bad","x"),null);
  const doneR=JSON.parse(JSON.stringify(fixture.result));
  doneR.drawing_model.dimensions.forEach((d) => { d.confirmed=true; });
  doneR.reviewDirty=false;                                           // an earlier case left unsaved edits on the shared fixture
  doneR.design={id:"x",model:doneR.drawing_model,plan:{status:"needs_review",questions:["19 readings hold several values or none in one callout (e.g. P1.d5)."],draft:null,provenance:{},calls:0},seconds:0};
  const doneCard=context.designCard(doneR);
  const panel=all(doneCard,(e) => e.attrs.class==="review-done")[0];
  assert.equal(panel.hidden,false,"all approved and nothing to answer: the review says it is complete");
  assert.ok(doneCard.textContent.includes("Review complete") && doneCard.textContent.includes("Notes for the 3D build"),"build notes are separate from questions");
  assert.equal(all(doneCard,(e) => e.tagName==="button" && e.textContent==="Go to SolidWorks →").length,1,"a complete review leads to the SolidWorks page");
  assert.equal(all(doneCard,(e) => (e.attrs.class||"").includes("m-qlink"))[0].hidden,true,"a build note is not counted as an open question");
  const showBody=html.slice(html.indexOf("function show(r)"),html.indexOf("function updateExtractionProgress"));
  assert.ok(!showBody.includes("assemblyCard(r)") && !showBody.includes("featureCard(r)") && !showBody.includes("assessmentCard(r)"),"review page omits the removed inspector sections");
  console.log("Drawing review UI smoke test passed");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
