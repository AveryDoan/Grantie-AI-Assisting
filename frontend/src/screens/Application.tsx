// The officer's guided view of one application: 1 Documents, 2 Redaction check, 3 Assessment, 4 Outcome.
// One compact header (who, which grant, status, steps, progress, one Continue button, and what is missing), then the step.
// A step is unlocked only when the previous step is Done; the officer can go back to any finished step and view it.
// Editing a finished step reopens the later steps (the server records that and leaves a notice here).
import { useEffect, useState } from "react";
import { api, type SubStepKey } from "../api";
import type { Navigate } from "../App";
import { APP_STATUS_LABEL, Button, ErrorNotice, Icon, Loading, formatDate, useLoad } from "../ui";
import Review from "./Review";
import { SEC, StepBar } from "./steps/parts";
import Step1Documents from "./steps/Step1Documents";
import Step2Redaction from "./steps/Step2Redaction";
import Step4Outcome from "./steps/Step4Outcome";

const BAND: Record<string, { icon: string; title: string; purpose: string }> = {
  "1": { icon: "file", title: "Documents", purpose: "Check the right documents are in the right place." },
  "2": { icon: "shield", title: "Redaction check", purpose: "Check what is hidden from the AI before it reads anything." },
  "4": { icon: "mail", title: "Outcome", purpose: "Review the result and the letter, then sign off." },
};
const BAND_SEC: Record<string, string> = { "1": "sec-docs", "2": "sec-redaction", "4": "sec-outcome" };
const SUB_KEYS: SubStepKey[] = ["eligibility", "consistency", "ai_use", "merit"];

export default function Application({ id, step, sub, navigate }: { id: string; step?: number; sub?: string; navigate: Navigate }) {
  const { data: st, error, reload } = useLoad(() => api.steps(id), [id]);
  const { data: detail, reload: reloadDetail } = useLoad(() => api.detail(id), [id]);
  const [view, setView] = useState<number | null>(step ?? null);
  const [subKey, setSubKey] = useState<SubStepKey | null>(SUB_KEYS.includes(sub as SubStepKey) ? (sub as SubStepKey) : null);
  const shown = view ?? st?.current ?? 1;
  const setSub = (k: SubStepKey) => { setSubKey(k); window.scrollTo({ top: 0 }); };

  useEffect(() => { if (step && step !== view) setView(step); }, [step]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (sub && SUB_KEYS.includes(sub as SubStepKey) && sub !== subKey) setSubKey(sub as SubStepKey); }, [sub]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the address in step with the screen (step, and the sub-step inside step 3), so a reload or a shared link returns there.
  useEffect(() => {
    if (view !== null && (view !== step || (view === 3 && subKey && subKey !== sub))) navigate({ name: "review", id, step: view, sub: view === 3 ? subKey ?? undefined : undefined });
  }, [view, subKey]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <main className="page"><ErrorNotice error={error} /></main>;
  if (!st || !detail) return <main className="page"><Loading /></main>;

  const app = detail.application;
  const info = st.steps.find((s) => s.step === shown) ?? st.steps[0];
  const go = (n: number) => { setView(n); window.scrollTo({ top: 0 }); };
  const changed = () => { reload(); reloadDetail(); };
  const advance = (n: number) => { changed(); go(n); };
  const step3 = st.steps.find((s) => s.step === 3);
  const subs = step3?.substeps ?? [];
  // The sub-step shown: the one asked for, else the first that still blocks the outcome, else the first.
  const currentSub = subs.find((s) => s.key === subKey) ?? subs.find((s) => s.blocking && s.status !== "done") ?? subs[0];
  const progress = step3?.progress ?? { decided: 0, total: 0 };
  const assessing = shown === 3 && info.unlocked && Boolean(detail.latest_run);

  // The sentence under the button: what is missing in the current sub-step, else what still blocks the outcome.
  const blocker = subs.find((s) => s.blocking && s.status !== "done");
  const missing = assessing ? (currentSub?.missing ?? blocker?.missing ?? null) : info.status === "done" ? null : info.missing[0] ?? null;
  const bandKey = String(shown);
  const band = BAND[bandKey] ?? BAND["1"];
  const pct = progress.total ? Math.round((progress.decided / progress.total) * 100) : 0;
  const ready = step3?.status === "done";

  return (
    <main className="gf-page">
      <header className={`gf-head ${SEC[shown]} ${shown === 3 ? "gf-head-compact" : ""}`}>
        <div className="gf-head-row">
          <div className="gf-id">
            <button className="link-button" onClick={() => navigate({ name: "queue" })}><Icon name="left" size={14} />Applications</button>
            <h1>{app.reference} · {app.applicant_name ?? "Applicant"}</h1>
            <p>{app.program_name}{detail.latest_run ? ` · Rule pack ${detail.latest_run.rule_pack_version}` : ""} · Received {formatDate(app.submitted_at)} · {APP_STATUS_LABEL[app.status] ?? app.status}</p>
          </div>
          <details className="gf-menu">
            <summary>More</summary>
            <div role="menu">
              <button role="menuitem" onClick={() => navigate({ name: "trace", id })}><Icon name="shield" size={15} />Redaction and AI trace</button>
              <button role="menuitem" onClick={() => navigate({ name: "audit" })}><Icon name="clock" size={15} />Audit trail</button>
            </div>
          </details>
        </div>
        <StepBar steps={st.steps} shown={shown} onGo={go} compact={shown === 3} />
        <div className="gf-progress">
          {detail.latest_run && (
            <p className="gf-count" aria-live="polite">
              <span className="gf-ring" style={{ ["--p" as string]: pct }} aria-hidden="true"><span>{pct}%</span></span>
              <span><strong>{progress.decided} of {progress.total}</strong> decided</span>
            </p>
          )}
          {assessing ? (
            <div className="gf-continue">
              <Button disabled={!ready} onClick={() => advance(4)} title={ready ? undefined : blocker?.missing ?? undefined}>
                Continue to Outcome <Icon name="arrow" />
              </Button>
              <small className="gf-missing-line">{missing ?? (ready ? (subs.some((x) => x.status === "not_assessed") ? "Not eligible (your decision). The other checks are not assessed." : "Eligibility and merit are complete.") : blocker?.missing)}</small>
            </div>
          ) : missing ? <small className="gf-missing-line">{missing}</small> : null}
        </div>
      </header>
      {info.notice && info.status !== "done" && <div className="notice warn-notice gf-notice" role="status"><Icon name="info" /><span>{info.notice}</span></div>}
      {info.status === "done" && shown < st.current && (
        <div className="notice blue gf-notice" role="status"><Icon name="info" /><span>You are looking at a finished step. If you change something here, the later steps need to be checked again.</span></div>
      )}
      {shown !== 3 && (      <section className={`gf-band ${BAND_SEC[bandKey]}`} aria-label={band.title}>
          <span className="gf-band-icon"><Icon name={band.icon} size={22} /></span>
          <h2>{band.title}</h2>
          {!assessing && (missing || info.status === "done") && <p className="gf-band-next">{missing ?? "Done"}</p>}
        </section>)}
      <div className="gf-body">
        <div className={`gf-main step-${shown}`}>
          {shown === 1 && <Step1Documents id={id} info={info} onChanged={changed} onAdvance={() => advance(2)} />}
          {shown === 2 && <Step2Redaction id={id} info={info} onChanged={changed} onAdvance={() => advance(3)} onBack={() => go(1)} />}
          {shown === 3 && (info.unlocked
            ? <Review key={`${id}-3`} detail={detail} reload={changed} sub={currentSub?.key ?? "eligibility"} setSub={setSub} subs={subs} onOutcome={() => advance(4)} navigate={navigate} />
            : <section className="panel empty-state"><p>Approve the redaction check (step 2) first. The AI cannot read this application before then.</p></section>)}
          {shown === 4 && (info.unlocked
            ? <Step4Outcome id={id} navigate={navigate} onChanged={changed} />
            : <section className="panel empty-state"><p>Finish the assessment (step 3) first: decide every rule and mark every merit criterion.</p></section>)}
        </div>
      </div>
    </main>
  );
}
