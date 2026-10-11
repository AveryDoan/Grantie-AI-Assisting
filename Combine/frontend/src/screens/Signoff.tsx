import { useState } from "react";
import { api } from "../api";
import type { Navigate } from "../App";
import { Button, ErrorNotice, Icon, Loading, StatusChip, effectiveStatus, formatDate, isDecided, useLoad } from "../ui";

export default function Signoff({ id, navigate }: { id: string; navigate: Navigate }) {
  const { data: detail, error, reload } = useLoad(() => api.detail(id), [id]);
  const { data: statement } = useLoad(api.signoffStatement, []);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);

  if (error) return <main className="page narrow"><ErrorNotice error={error} /></main>;
  if (!detail) return <main className="page narrow"><Loading /></main>;

  const app = detail.application;
  const findings = detail.findings;
  const decidedCount = findings.filter(isDecided).length;
  const remaining = findings.length - decidedCount;
  const signedOff = !!detail.sign_off;
  const noRun = !detail.latest_run && !app.manual_assessment_requested;
  const ready = !signedOff && !noRun && remaining === 0;

  const signOff = async () => {
    setBusy(true);
    setFailure(null);
    try {
      await api.signoff(id);
      reload();
    } catch (e) {
      setFailure(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="page narrow">
      <button className="back-link" onClick={() => navigate({ name: "review", id })}><Icon name="left" />Back to application review</button>
      <div className="page-heading">
        <div><p className="eyebrow">Application {app.reference}</p><h1>Sign-off check</h1><p>Check every finding before you sign off.</p></div>
        <span className="draft-chip">{signedOff ? `Signed off ${formatDate(detail.sign_off!.signed_at)}` : "Not signed off"}</span>
      </div>
      <div className="signoff-layout">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Your findings</h2>
              <p>{noRun ? "This application has not been checked yet." : remaining ? `${remaining} rule${remaining === 1 ? "" : "s"} still need your decision` : "Every rule has your decision"}</p>
            </div>
            <span className={remaining || noRun ? "review-count incomplete" : "review-count complete"}>
              <Icon name={remaining || noRun ? "clock" : "check"} />{decidedCount} of {findings.length}
            </span>
          </div>
          <div className="checklist">
            {findings.map((f, i) => {
              const done = isDecided(f);
              return (
                <div className="check-row" key={f.id}>
                  <span className={done ? "check-circle done" : "check-circle"}>{done ? <Icon name="check" /> : i + 1}</span>
                  <div>
                    <strong>{f.rule_code} · {f.rule_text}</strong>
                    <small>
                      {done
                        ? `Decided ${formatDate(f.latest_review!.reviewed_at, true)}${f.latest_review!.reason ? ` – “${f.latest_review!.reason}”` : ""}`
                        : f.latest_review?.action === "ask_applicant" ? "Waiting for applicant – still needs your decision" : "Decision needed"}
                    </small>
                  </div>
                  <StatusChip status={effectiveStatus(f)} />
                  {!done && !signedOff && <Button variant="secondary" onClick={() => navigate({ name: "review", id })}>Review</Button>}
                </div>
              );
            })}
          </div>
        </section>
        <aside className="decision-card">
          <span className="large-icon"><Icon name="shield" size={28} /></span>
          <h2>Your sign-off</h2>
          <p>You are responsible for this decision. AI suggestions are not decisions.</p>
          {statement && <p className="statement">{statement.statement}</p>}
          <label className="confirm-check">
            <input type="checkbox" disabled={!ready} checked={acknowledged || signedOff} onChange={(e) => setAcknowledged(e.target.checked)} />
            <span>I have checked every rule and the evidence used.</span>
          </label>
          <ErrorNotice error={failure} />
          {signedOff ? (
            <Button onClick={() => navigate({ name: "letter", id })}>Go to outcome letter <Icon name="arrow" /></Button>
          ) : (
            <Button disabled={!ready || !acknowledged || busy} onClick={() => void signOff()}>Sign off decision</Button>
          )}
          {!ready && !signedOff && <small className="locked"><Icon name="clock" />Sign-off unlocks when every rule has your decision.</small>}
        </aside>
      </div>
    </main>
  );
}
