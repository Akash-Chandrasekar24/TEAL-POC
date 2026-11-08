import React, { useLayoutEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./studio.css";

// Build and Verify are one step: the build card (progress, fix approvals, retries) and the model check share a page,
// which opens only once SolidWorks has something to show. The four human pauses are unchanged; only where they appear moved.
const FLOW = ["Upload", "Review", "Build & Verify", "Export"];

function App() {
  const [step, setStep] = useState(location.hash ? 2 : 1);
  const [hasResult, setHasResult] = useState(false);
  const [phase, setPhase] = useState("");
  const [busy, setBusy] = useState(null);
  const [phaseError, setPhaseError] = useState("");
  const [, refreshLayout] = useState(0);
  const statistics = useRef(null);
  const upload = useRef(null), extract = useRef(null), reviewChat = useRef(null), verify = useRef(null), exportSlot = useRef(null);

  const building = Boolean(phase);                                   // a SolidWorks job exists (building, paused, preview, accepted ...)
  const exportReady = ["accepted", "exported"].includes(phase);

  function placeLegacyNodes() {
    const move = (node, host) => { if (node && host && node.parentNode !== host) host.appendChild(node); };
    document.body.dataset.step = String(step);
    move(document.querySelector("#upload-panel"), upload.current);
    move(document.querySelector("#main"), extract.current);
    move(document.querySelector("#summary"), statistics.current);
    move(document.querySelector("#agent-chat"), document.querySelector("#review-conversation-slot") || reviewChat.current);
    const job = document.querySelector("#workflow-job");
    if (job) move(job, step === 4 || (step < 3 && exportReady) ? exportSlot.current : verify.current);
  }

  useLayoutEffect(() => { placeLegacyNodes(); });

  useLayoutEffect(() => {
    const onResult = (event) => {
      setHasResult(true);
      const job = event.detail?.job;
      setPhase(job?.phase || event.detail?.phase || "");             // a newly opened drawing must not inherit another one's build state
      setPhaseError("");
      setStep((current) => current === 1 ? 2 : current);
      refreshLayout((value) => value + 1);
    };
    const onJob = (event) => {
      const job = event.detail?.job || event.detail || {};
      if (job.phase) {
        setPhase(job.phase);
        setPhaseError("");
        if (["accepted", "exported"].includes(job.phase)) setStep(4);
        else if (job.phase === "awaiting_review") setStep(3);
      }
    };
    const onProcessing = (event) => setBusy(event.detail?.busy ? event.detail : null);
    const onError = (event) => setPhaseError(event.detail?.message || "The operation could not be completed.");
    window.addEventListener("studio:result", onResult);
    window.addEventListener("studio:job", onJob);
    window.addEventListener("studio:processing", onProcessing);
    window.addEventListener("studio:error", onError);
    const hasExisting = Boolean(document.querySelector("#main")?.dataset.resultId);
    if (hasExisting) {
      setHasResult(true);
      setStep(2);
      placeLegacyNodes();
    }
    window.studioNavigate = (page) => setStep(page);
    window.addEventListener("hashchange", () => { if (location.hash) setStep((current) => current === 1 ? 2 : current); });
    return () => {
      window.removeEventListener("studio:result", onResult);
      window.removeEventListener("studio:job", onJob);
      window.removeEventListener("studio:processing", onProcessing);
      window.removeEventListener("studio:error", onError);
      delete window.studioNavigate;
    };
  }, []);

  const canVisit = (index) => index === 1 || (index === 2 && hasResult) || (index === 3 && hasResult && building) || (index === 4 && hasResult && exportReady);
  const titles = {
    1: ["From drawing to dimension.", "Start with your drawing. Shape what comes next."],
    2: ["Your drawing. Understood.", "Confident readings move forward. Review the exceptions and talk through the details with your agent."],
    3: ["Build it. Check it. Confirm it.", "Your SolidWorks agent builds the approved plan, pauses whenever it needs your input, then shows the model beside your drawing."],
    4: ["Ready for what’s next.", "Export your confirmed model in the format you need."]
  };
  const move = (value) => setStep(value);

  return <>
    <header className="studio-header">
      <div className="brand-lockup"><img className="brand-logo" src="/gwc-logo.svg" alt="GWC Data.Ai"/><h1>Drawing Studio</h1></div>
      <div className="header-actions"><button type="button" className="history-btn">Recent drawings</button><div className="header-status">SolidWorks workspace</div></div>
    </header>
    <div className="studio-frame">
      <nav aria-label="Drawing workflow"><ol className="stepper">{FLOW.map((label, i) => {
        const page = i + 1, available = canVisit(page), active = page === step, done = (page === 1 && hasResult) || (page === 2 && hasResult && step > 2) || (page === 3 && exportReady) || (page === 4 && phase === "exported");
        return <li key={label}><button type="button" className={`stepper-item${active ? " active" : ""}${done ? " done" : ""}${available ? " available" : ""}`} aria-current={active ? "step" : undefined} aria-disabled={!available} onClick={() => available && move(page)}><span className="step-dot">{done ? "✓" : page}</span><span>{label}</span></button></li>;
      })}</ol></nav>
      <div className={`studio-intro${step === 1 ? " welcome" : ""}`}><span className="eyebrow">{step === 1 ? "YOUR NEXT CREATION" : `STEP ${step} OF ${FLOW.length} · ${FLOW[step-1]}`}</span><h2>{titles[step][0]}</h2><p>{titles[step][1]}</p></div>

      <section className={`studio-page${step === 1 ? " active" : ""}`} aria-label="Upload drawing">
        <div className="upload-layout"><div id="upload-slot" ref={upload}/><p className="upload-assurance">Review the measurements. Approve the build. Confirm before export.</p></div>
      </section>

      <section className={`studio-page${step === 2 ? " active" : ""}`} aria-label="Extraction and measurement review">
        <div className="stage-grid review-stage"><div id="extract-slot" className="stage-slot" ref={extract}/><aside className="stage-slot" ref={reviewChat}/></div>
        {phaseError && step === 2 && <div className="msg err" role="alert">{phaseError}</div>}
        <div hidden ref={statistics}/>
        <div className="step-footer"><span className="step-footnote">Everything to review, including open questions, is on this page. A 3D build starts from “Approve features and build” once the plan is ready; “Finish without a 3D build” keeps just the measurements.</span><div className="actions"><button className="btn secondary" type="button" onClick={() => move(1)}>Back</button>{building && <button className="btn" type="button" onClick={() => move(3)}>Go to build & verify <span aria-hidden="true">→</span></button>}</div></div>
      </section>

      <section className={`studio-page${step === 3 ? " active" : ""}`} aria-label="SolidWorks build and verification">
        <div className="workflow-slot" ref={verify}/>
        {phaseError && step === 3 && <div className="msg err" role="alert">{phaseError}</div>}
        <div className="step-footer"><button className="btn secondary" type="button" onClick={() => move(2)}>Back to review</button><span className="step-footnote">Approve any fix the agent proposes here; accept the model to unlock export.</span></div>
      </section>

      <section className={`studio-page${step === 4 ? " active" : ""}`} aria-label="Export confirmed model">
        <div className="workflow-slot" ref={exportSlot}/>
        {phaseError && step === 4 && <div className="msg err" role="alert">{phaseError}</div>}
        <div className="step-footer"><button className="btn secondary" type="button" onClick={() => move(3)}>Back to build & verify</button><span className="step-footnote">Downloads are available after you confirm the comparison.</span></div>
      </section>
    </div>
    {busy && <div className="processing-overlay" role="status" aria-live="polite"><div className={`processing-card${busy.pages?.length ? " with-pages" : ""}`}><div className="processing-spinner" aria-hidden="true"/><h2>{busy.title || "Processing drawing"}</h2><p>{busy.message || "The agents are working through this step."}</p>
      {busy.pages?.length > 0 && <div className="processing-pages"><p>Inspect completed pages now. Measurement editing opens when the full set is ready.</p>{busy.pages.map(page => <details key={page.no}><summary>Page {page.no} · {page.boxes.length} views · {page.read?.dims.length || 0} measurements</summary><img src={`/api/page/${page.sha}`} alt={`Completed drawing page ${page.no}`} loading="lazy"/><p>{page.read?.dims.map(d => `${d.id}: ${d.text}`).join(" · ")}</p></details>)}</div>}
      <small>Keep this page open while the remaining readings finish.</small><div className="processing-track"/></div></div>}
  </>;
}

createRoot(document.getElementById("studio-root")).render(<App/>);
