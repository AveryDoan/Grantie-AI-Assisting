// The officer's guided view of one application: 1 Documents, 2 Redaction check, 3 Assessment, 4 Outcome.
// A step is unlocked only when the previous step is Done; the officer can go back to any finished step and view it.
// Editing a finished step reopens the later steps (the server records that and leaves a notice here).
import { useEffect, useState } from "react";
import { api } from "../api";
import type { Navigate } from "../App";
import { ErrorNotice, Icon, Loading, useLoad } from "../ui";
import Review from "./Review";
import { NextStep, StepBar } from "./steps/parts";
import Step1Documents from "./steps/Step1Documents";
import Step2Redaction from "./steps/Step2Redaction";
import Step4Outcome from "./steps/Step4Outcome";

export default function Application({ id, step, navigate }: { id: string; step?: number; navigate: Navigate }) {
  const { data: st, error, reload } = useLoad(() => api.steps(id), [id]);
  const [view, setView] = useState<number | null>(step ?? null);
  const shown = view ?? st?.current ?? 1;

  useEffect(() => { if (step && step !== view) setView(step); }, [step]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the address in step with the screen, so a reload or a shared link returns to the same step.
  useEffect(() => {
    if (view !== null && view !== step) navigate({ name: "review", id, step: view });
  }, [view]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <main className="page"><ErrorNotice error={error} /></main>;
  if (!st) return <main className="page"><Loading /></main>;

  const info = st.steps.find((s) => s.step === shown) ?? st.steps[0];
  const go = (n: number) => { setView(n); window.scrollTo({ top: 0 }); };
  const changed = () => reload();
  const advance = (n: number) => { reload(); go(n); };

  return (
    <main className="gf-page">
      <StepBar steps={st.steps} shown={shown} onGo={go} />
      {info.notice && info.status !== "done" && <div className="notice warn-notice gf-notice" role="status"><Icon name="info" /><span>{info.notice}</span></div>}
      {info.status === "done" && shown < st.current && (
        <div className="notice blue gf-notice" role="status"><Icon name="info" /><span>You are looking at a finished step. If you change something here, the later steps need to be checked again.</span></div>
      )}
      <div className="gf-body">
        <div className="gf-main">
          {shown === 1 && <Step1Documents id={id} info={info} onChanged={changed} onAdvance={() => advance(2)} />}
          {shown === 2 && <Step2Redaction id={id} info={info} onChanged={changed} onAdvance={() => advance(3)} onBack={() => go(1)} />}
          {shown === 3 && (info.unlocked
            ? <>
                <Review key={`${id}-3`} id={id} navigate={navigate} embedded={{ onNext: () => advance(4), onChanged: changed, nextReady: info.status === "done" }} />
                <NextStep label="Next step: Outcome" missing={info.missing} onClick={() => advance(4)}
                  note="Every rule is decided and every merit criterion is marked or set to Not assessed." />
              </>
            : <section className="panel empty-state"><p>Approve the redaction check (step 2) first. The AI cannot read this application before then.</p></section>)}
          {shown === 4 && (info.unlocked
            ? <Step4Outcome id={id} navigate={navigate} onChanged={changed} />
            : <section className="panel empty-state"><p>Finish the assessment (step 3) first: decide every rule and mark every merit criterion.</p></section>)}
        </div>
      </div>
    </main>
  );
}
