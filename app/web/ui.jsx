import React, { useLayoutEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./studio.css";

// Three pages: Upload -> Review (AI reading, questions, your answers) -> SolidWorks (parts, build lists, one approval, build, check, export).
// SolidWorks opens once the review is complete (or a build already exists). The four human pauses are unchanged.
const FLOW = ["Upload", "Review", "SolidWorks"];

function App() {
  const [step, setStep] = useState(location.hash ? 2 : 1);
  const [hasResult, setHasResult] = useState(false);
  const [phase, setPhase] = useState("");
  const [busy, setBusy] = useState(null);
  const [phaseError, setPhaseError] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [, refreshLayout] = useState(0);
  const statistics = useRef(null);
  const upload = useRef(null), extract = useRef(null), reviewChat = useRef(null), solidworks = useRef(null);

  const building = Boolean(phase);                                   // a SolidWorks job exists (building, paused, preview, accepted ...)

  function placeLegacyNodes() {
    const move = (node, host) => { if (node && host && node.parentNode !== host) host.appendChild(node); };
    document.body.dataset.step = String(step);
    move(document.querySelector("#upload-panel"), upload.current);
    move(document.querySelector("#main"), extract.current);
    move(document.querySelector("#summary"), statistics.current);
    move(document.querySelector("#agent-chat"), document.querySelector("#review-conversation-slot") || reviewChat.current);
    move(document.querySelector("#solidworks-page"), solidworks.current);
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
        if (job.phase === "awaiting_review") setStep(3);
      }
    };
    const onProcessing = (event) => setBusy(event.detail?.busy ? event.detail : null);
    const onError = (event) => setPhaseError(event.detail?.message || "The operation could not be completed.");
    const onReviewed = (event) => setReviewed(Boolean(event.detail?.done));
    window.addEventListener("studio:result", onResult);
    window.addEventListener("studio:job", onJob);
    window.addEventListener("studio:processing", onProcessing);
    window.addEventListener("studio:error", onError);
    window.addEventListener("studio:reviewed", onReviewed);
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
      window.removeEventListener("studio:reviewed", onReviewed);
      delete window.studioNavigate;
    };
  }, []);

  const canVisit = (index) => index === 1 || (index === 2 && hasResult) || (index === 3 && hasResult && (reviewed || building));
  const titles = {
    1: ["From drawing to dimension.", "Start with your drawing. Shape what comes next."],
    2: ["Your drawing. Understood.", "Confident readings move forward. Review the exceptions and talk through the details with your agent."],
    3: ["SolidWorks. Build it, check it, export it.", "Every part gets a build list, you approve once, SolidWorks builds each part in 3D. Compare each model with your drawing, then export."]
  };
  const move = (value) => setStep(value);

  return <>
    <header className="studio-header">
      <div className="brand-lockup"><img className="brand-logo" src="/gwc-logo.svg" alt="GWC Data.Ai"/><h1>Drawing Studio</h1></div>
      <div className="header-actions"><button type="button" className="history-btn">Recent drawings</button><div className="header-status">SolidWorks workspace</div></div>
    </header>
    <div className="studio-frame">
      <nav aria-label="Drawing workflow"><ol className="stepper">{FLOW.map((label, i) => {
        const page = i + 1, available = canVisit(page), active = page === step, done = (page === 1 && hasResult) || (page === 2 && reviewed) || (page === 3 && phase === "exported");
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
        <div className="step-footer"><span className="step-footnote">Approve the measurements, then answer the AI's questions. SolidWorks opens when the review is complete.</span><div className="actions"><button className="btn secondary" type="button" onClick={() => move(1)}>Back</button>{(reviewed || building) && <button className="btn" type="button" onClick={() => move(3)}>Go to SolidWorks <span aria-hidden="true">→</span></button>}</div></div>
      </section>

      <section className={`studio-page${step === 3 ? " active" : ""}`} aria-label="SolidWorks: build, check and export">
        <div className="workflow-slot" ref={solidworks}/>
        {phaseError && step === 3 && <div className="msg err" role="alert">{phaseError}</div>}
        <div className="step-footer"><button className="btn secondary" type="button" onClick={() => move(2)}>Back to review</button><span className="step-footnote">Nothing is built until you approve. Accept a model to unlock its export.</span></div>
      </section>
    </div>
    {busy && <div className="processing-overlay" role="status" aria-live="polite"><div className={`processing-card${busy.pages?.length ? " with-pages" : ""}`}><div className="processing-spinner" aria-hidden="true"/><h2>{busy.title || "Processing drawing"}</h2><p>{busy.message || "The agents are working through this step."}</p>
      {busy.pages?.length > 0 && <div className="processing-pages"><p>Inspect completed pages now. Measurement editing opens when the full set is ready.</p>{busy.pages.map(page => <details key={page.no}><summary>Page {page.no} · {page.boxes.length} views · {page.read?.dims.length || 0} measurements</summary><img src={`/api/page/${page.sha}`} alt={`Completed drawing page ${page.no}`} loading="lazy"/><p>{page.read?.dims.map(d => `${d.id}: ${d.text}`).join(" · ")}</p></details>)}</div>}
      <small>Keep this page open while the remaining readings finish.</small><div className="processing-track"/></div></div>}
  </>;
}

createRoot(document.getElementById("studio-root")).render(<App/>);
