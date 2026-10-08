import { useState, type ReactNode } from "react";
import { api, type Letter } from "../api";
import type { Navigate } from "../App";
import studyNtLogo from "../assets/study-nt-logo.svg";
import { Button, ErrorNotice, Icon, Loading, formatDate, useLoad } from "../ui";

const LABELS = [
  "What the rule requires:",
  "Why this did not meet the rule:",
  "What would change the outcome:",
  "How to ask for a review:",
];

/** Render the fixed-structure letter text produced by the API. */
function LetterBody({ text }: { text: string }) {
  const lines = text.split("\n");
  const out: ReactNode[] = [];
  lines.forEach((line, i) => {
    if (!line.trim()) return;
    if (i === 0) { out.push(<h2 key={i}>{line}</h2>); return; }
    if (/^Reason \d+: /.test(line)) { out.push(<h3 key={i}>{line}</h3>); return; }
    if (line === "How to ask for a review") { out.push(<h3 key={i}>{line}</h3>); return; }
    const quote = line.match(/^What you wrote: "(.*)"$/);
    if (quote) {
      out.push(<p key={`${i}l`}><strong>What you wrote</strong></p>, <blockquote key={i}>“{quote[1]}”</blockquote>);
      return;
    }
    if (line.startsWith("What you wrote: ")) {
      out.push(<p key={i}><strong>What you wrote</strong><br />{line.slice(16)}</p>);
      return;
    }
    const label = LABELS.find((l) => line.startsWith(l));
    if (label) {
      out.push(<p key={i}><strong>{label.slice(0, -1)}</strong><br />{line.slice(label.length).trim()}</p>);
      return;
    }
    out.push(<p key={i}>{line}</p>);
  });
  return <>{out}</>;
}

function Check({ ok, children }: { ok: boolean; children: ReactNode }) {
  return <div className={`quality-item ${ok ? "" : "warn"}`}><Icon name={ok ? "check" : "close"} /><span>{children}</span></div>;
}

function QualityPanel({ letter }: { letter: Letter }) {
  const q = letter.quality_checks;
  const all = (k: keyof (typeof q.checklist)[number]) => q.checklist.length > 0 && q.checklist.every((c) => c[k]);
  return (
    <>
      <div className="quality-score">
        <span>Aa</span>
        <div><small>Reading grade</small><strong>{q.reading_grade}</strong>
          <p>{q.reading_grade_ok ? `At or below the target (${q.reading_grade_target}, about Year 8)` : `Above the target of ${q.reading_grade_target} – simplify the wording`}</p></div>
      </div>
      <h2>Reason quality</h2>
      <Check ok={all("cites_rule")}>States what the rule requires</Check>
      <Check ok={all("quotes_applicant")}>Quotes the applicant’s words</Check>
      <Check ok={all("explains_link")}>Explains why the rule was not met</Check>
      <Check ok={all("says_what_would_change")}>Says what could change the outcome</Check>
      <Check ok={q.includes_review_info}>Explains how to ask for a review</Check>
      <h2>Required checks</h2>
      <Check ok={q.quotes_match_application}>Every quote matches the application{q.unmatched_quotes.length ? ` (${q.unmatched_quotes.length} do not)` : ""}</Check>
      <Check ok={q.every_reason_cites_confirmed_rule}>Every reason cites a rule you decided</Check>
      <Check ok={q.no_score_or_ranking_language}>No score or ranking language</Check>
    </>
  );
}

export default function LetterScreen({ id, navigate }: { id: string; navigate: Navigate }) {
  const { data: detail, error, reload } = useLoad(() => api.detail(id), [id]);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);

  if (error) return <main className="page wide-page"><ErrorNotice error={error} /></main>;
  if (!detail) return <main className="page wide-page"><Loading /></main>;

  const app = detail.application;
  const letter = [...detail.letters].sort((a, b) => b.version - a.version)[0] as Letter | undefined;
  const approved = letter?.status === "approved";
  const signedOff = app.status === "signed_off";

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setFailure(null);
    try { await fn(); setEditing(false); reload(); } catch (e) { setFailure(e); } finally { setBusy(false); }
  };

  const chip = !letter ? "No draft yet" : approved ? `Approved ${formatDate(letter.approved_at)}` : letter.status === "edited" ? "Edited draft – not sent" : "Draft – not sent";

  return (
    <main className="page wide-page">
      <div className="page-heading">
        <div>
          <button className="back-link" onClick={() => navigate({ name: "signoff", id })}><Icon name="left" />Sign-off</button>
          <p className="eyebrow">Application {app.reference}{letter ? ` · Version ${letter.version}` : ""}</p>
          <h1>Outcome letter</h1>
          <p>Only findings decided by an officer are included. The letter is written from a fixed template; quotes are checked against the application.</p>
        </div>
        <span className="draft-chip">{chip}</span>
      </div>
      <ErrorNotice error={failure} />
      {!letter ? (
        <section className="panel empty-state">
          <p>No letter has been drafted. A reasons letter lists each rule you decided was “Not met” or “Needs evidence”.</p>
          <Button icon="file" disabled={busy} onClick={() => void act(() => api.generateLetter(id))}>Draft letter from my decisions</Button>
        </section>
      ) : (
        <div className="letter-layout">
          <section className="letter-paper">
            <div className="letter-mast">
              <img className="letter-logo" src={studyNtLogo} alt="Study NT" />
              <div><strong>{app.program_name}</strong><small>Sample correspondence · fictional</small></div>
            </div>
            {editing
              ? <textarea className="letter-editor" rows={30} value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Letter text" />
              : <LetterBody text={letter.body_text} />}
          </section>
          <aside className="quality-panel">
            <QualityPanel letter={letter} />
            <div className="notice"><Icon name="info" />Check names, dates and contact details before approving.</div>
            {!approved && (editing ? (
              <>
                <Button icon="check" disabled={busy} onClick={() => void act(() => api.patchLetter(letter.id, { body_text: draft }))}>Save changes</Button>
                <Button variant="secondary" onClick={() => setEditing(false)}>Cancel</Button>
              </>
            ) : (
              <Button variant="secondary" icon="edit" onClick={() => { setDraft(letter.body_text); setEditing(true); }}>Edit letter</Button>
            ))}
            <Button variant="secondary" icon="download" onClick={() => window.print()}>Print / save as PDF</Button>
            {!approved && (
              <Button icon="check" disabled={busy || editing || !signedOff}
                title={signedOff ? undefined : "Sign off the application before approving its letter"}
                onClick={() => void act(() => api.patchLetter(letter.id, { approve: true }))}>Approve letter</Button>
            )}
            {!approved && !signedOff && <small className="locked"><Icon name="clock" />Approval unlocks after sign-off. The applicant only sees an approved letter.</small>}
            {!approved && <Button variant="quiet" disabled={busy} onClick={() => void act(() => api.generateLetter(id))}>Regenerate from decisions</Button>}
            <Button onClick={() => navigate({ name: "audit" })}>Continue to audit trail <Icon name="arrow" /></Button>
          </aside>
        </div>
      )}
    </main>
  );
}
