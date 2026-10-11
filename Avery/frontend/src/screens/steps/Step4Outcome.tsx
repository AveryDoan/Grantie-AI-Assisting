// Step 4: Outcome. A decline letter (when a rule was confirmed as Not met) or the eligible outcome with a panel summary and a
// next-steps letter. The letter is released only after the officer signs off. There is no "approve all", no bulk sign-off, no
// total, average or ranking. After sign-off the record is locked; reopening needs a reason and makes the next sign-off a new version.
import { useState } from "react";
import { api, type Outcome } from "../../api";
import type { Navigate } from "../../App";
import { Button, ErrorNotice, Icon, Loading, formatDate, useLoad } from "../../ui";
import { LetterEditor } from "../Letter";

function Unmet({ items, title }: { items: Outcome["unmet"]; title: string }) {
  if (!items.length) return null;
  return (
    <section className="panel">
      <div className="panel-heading"><div><h2>{title}</h2></div></div>
      <ul className="gf-unmet">
        {items.map((u) => (
          <li key={u.rule_code}>
            <strong>{u.rule_text}</strong>
            {u.quote ? <blockquote>“{u.quote}”</blockquote> : <p className="muted">Nothing in the application covers this.</p>}
            {u.reason && <p><strong>Why:</strong> {u.reason}</p>}
            {u.what_would_change && <p><strong>What would need to be different:</strong> {u.what_would_change}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function Step4Outcome({ id, navigate, onChanged }: { id: string; navigate: Navigate; onChanged: () => void }) {
  const { data: out, error, reload: reloadOut } = useLoad(() => api.outcome(id), [id]);
  const { data: detail, reload: reloadDetail } = useLoad(() => api.detail(id), [id]);
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [reopening, setReopening] = useState(false);
  const [reason, setReason] = useState("");
  if (error) return <ErrorNotice error={error} />;
  if (!out || !detail) return <Loading />;

  const reload = () => { reloadOut(); reloadDetail(); onChanged(); };
  const act = async (fn: () => Promise<unknown>) => { setBusy(true); setFailure(null); try { await fn(); reload(); } catch (e) { setFailure(e); } setBusy(false); };
  const signedOff = out.signed_off;
  const kind = out.letter_kind;
  const program = detail.application.program_name;

  return (
    <>
      <section className="panel">
        <div className="panel-heading"><div><p className="eyebrow">Step 4 of 4</p><h2>Outcome</h2>
          <p>{out.result === "decline" ? "One or more eligibility rules were confirmed as Not met."
            : out.result === "eligible" ? "Every eligibility rule is met, or settled by you."
            : out.result === "needs_information" ? "Some rules still need more information from the applicant."
            : "Not every rule has your decision yet."}</p></div>
          <span className={`gf-chip ${out.result === "decline" ? "bad" : out.result === "eligible" ? "ok" : "wait"}`}>
            {out.result === "decline" ? "Not eligible" : out.result === "eligible" ? "Eligible" : out.result === "needs_information" ? "More information needed" : "Undecided"}</span></div>
        {out.result === "needs_information" && (
          <div className="notice"><Icon name="info" /><span>Go back to ask the applicant for what is missing: <button className="link-button" onClick={() => navigate({ name: "review", id, step: 1 })}>Step 1: Documents</button> or <button className="link-button" onClick={() => navigate({ name: "review", id, step: 3 })}>Step 3: Assessment</button>.</span></div>
        )}
      </section>

      <Unmet items={out.unmet} title="Rules confirmed as Not met" />
      <Unmet items={out.needs_information} title="Rules that need more information" />

      {out.summary && (
        <section className="panel">
          <div className="panel-heading"><div><h2>Summary for the panel</h2><p>{out.summary.note}</p></div></div>
          <div className="rv-table-wrap"><table className="rv-table">
            <thead><tr><th>Criterion</th><th>Officer mark</th><th>Reason</th></tr></thead>
            <tbody>{out.summary.marks.map((m) => (
              <tr key={m.rule_code}><td><strong>{m.criterion.replace(/\s*Weight on the form: \d+%\.?/i, "").replace(/\s*\(Officer judgement\.\)/i, "")}</strong></td>
                <td>{m.not_assessed ? "Not assessed" : `${m.mark} out of 100`}</td><td>{m.reason ?? "–"}</td></tr>))}</tbody>
          </table></div>
          <h3>Signals you kept for follow-up</h3>
          {out.summary.signals_kept.length === 0 ? <p className="muted">None.</p> : <ul className="cx-list">{out.summary.signals_kept.map((s, i) => <li key={i}>{s.label}{s.note ? `: ${s.note}` : ""}</li>)}</ul>}
        </section>
      )}

      {(out.result === "decline" || out.result === "eligible") && (
        <section className="panel">
          <div className="panel-heading"><div><h2>{kind === "decline" ? "Decline letter" : "Next steps letter"}</h2>
            <p>{kind === "decline" ? "Drafted when you confirmed a rule as Not met. It names each unmet rule, quotes the applicant’s own words and says what would need to be different."
              : "Tells the applicant what happens next."} You review and edit it. It is released only after you sign off.</p></div></div>
          {out.letter
            ? <LetterEditor letter={out.letter} programName={program} signedOff={signedOff} onChanged={reload}
                onRegenerate={kind === "decline" ? () => api.generateLetter(id) : () => api.nextStepsLetter(id)} />
            : <div className="empty-state"><p>No letter has been drafted yet.</p>
                <Button icon="file" disabled={busy} onClick={() => void act(() => (kind === "decline" ? api.generateLetter(id) : api.nextStepsLetter(id)))}>Draft the letter</Button></div>}
        </section>
      )}

      <section className="panel gf-signoff">
        <div className="panel-heading"><div><h2>Sign-off</h2><p>You are responsible for this decision. AI suggestions are not decisions.</p></div></div>
        <ErrorNotice error={failure} />
        {!signedOff ? (
          <>
            <p className="statement">{out.statement}</p>
            <label className="confirm-check"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /><span>I have checked every rule and the evidence used.</span></label>
            <Button disabled={!ack || busy || out.result === "undecided" || out.result === "not_assessed"} onClick={() => void act(() => api.signoff(id))}>Sign off this application</Button>
            <small className="muted">Each application is signed off on its own. There is no bulk sign-off.</small>
          </>
        ) : out.record && (
          <div className="gf-record">
            <h3>Final record</h3>
            <dl className="rv-facts">
              <div><dt>Outcome</dt><dd>{out.record.outcome === "decline" ? "Not eligible" : out.record.outcome === "eligible" ? "Eligible" : out.record.outcome}</dd></div>
              <div><dt>Sign-off version</dt><dd>{out.record.version}</dd></div>
              <div><dt>Officer</dt><dd>{out.record.officer}</dd></div>
              <div><dt>Signed</dt><dd>{formatDate(out.record.signed_at, true)}</dd></div>
              <div><dt>Letter</dt><dd>{out.record.letter_version ? `Version ${out.record.letter_version} (${out.record.letter_status})` : "None"}</dd></div>
            </dl>
            <div className="rv-table-wrap"><table className="rv-table">
              <thead><tr><th>Rule</th><th>Decision</th><th>Evidence</th></tr></thead>
              <tbody>{out.record.decisions.map((d) => (
                <tr key={d.rule_code}><td><strong>{d.rule_code}</strong> {d.rule_text.replace(/\s*Weight on the form: \d+%\.?/i, "")}</td>
                  <td>{d.decision}{d.overridden ? " (you changed the AI’s suggestion)" : ""}{d.reason ? <small className="rv-reasonline">{d.reason}</small> : null}</td>
                  <td><button className="link-button" onClick={() => navigate({ name: "trace", id, item: `rule:${d.rule_code}` })}>Open evidence <Icon name="arrow" size={14} /></button></td></tr>))}</tbody>
            </table></div>
            <Button variant="secondary" icon="file" onClick={() => void act(async () => {
              // The decisions and the audit entries (ids, counts and codes only, never personal values). Not scores.
              const audit = await api.audit({ application_id: id });
              const blob = new Blob([JSON.stringify({ record: out.record, audit }, null, 2)], { type: "application/json" });
              const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `record-${id.slice(0, 8)}.json`; a.click(); URL.revokeObjectURL(a.href);
            })}>Export record</Button>
            {out.record.reopened.length > 0 && <><h3>Earlier versions</h3><ul className="cx-list">{out.record.reopened.map((r) => <li key={r.version}>Version {r.version} was reopened {formatDate(r.reopened_at, true)}: {r.reason}</li>)}</ul></>}
            {!reopening ? <Button variant="secondary" onClick={() => setReopening(true)}>Reopen this application</Button> : (
              <div className="gf-inline">
                <label className="field"><span>Why are you reopening it? <b>*</b></span><textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
                <Button disabled={busy || !reason.trim()} onClick={() => void act(async () => { await api.reopen(id, reason.trim()); setReopening(false); setReason(""); })}>Reopen with this reason</Button>
                <Button variant="quiet" onClick={() => setReopening(false)}>Cancel</Button>
              </div>
            )}
          </div>
        )}
      </section>
    </>
  );
}
