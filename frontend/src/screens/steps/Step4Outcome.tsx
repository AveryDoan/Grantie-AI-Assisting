// Step 4: Outcome. The letter on the left; on the right the confirmed Not met reasons (not eligible), the officer's feedback
// choices (not selected) or the round decision (selected), and the sign-off checklist. One primary button: "Sign off and release
// letter", enabled only when the letter was read to the end and every check passes. Nothing is released automatically; there is
// no bulk sign-off, no score, no rank and no comparison. After sign-off the record is locked; a change needs a reconsideration.
// Keyboard: R = mark the letter as read (after reaching its end), Ctrl+Enter = sign off and release.
import { useEffect, useRef, useState } from "react";
import { api, type Letter, type Outcome } from "../../api";
import type { Navigate } from "../../App";
import { Button, ErrorNotice, Icon, Loading, formatDate, useLoad } from "../../ui";

const PLACEHOLDER = /(\[[^[\]\n]{2,80}\])/;          // split on it (the capture keeps the placeholder)
const IS_PLACEHOLDER = /^\[[^[\]\n]{2,80}\]$/;
const KIND_TITLE: Record<string, string> = { decline: "Letter: not eligible", not_selected: "Letter: not selected", next_steps: "Letter: next steps" };

function timeOf(iso?: string | null) {
  return iso ? new Date(iso).toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit" }) : "";
}

function LetterText({ text }: { text: string }) {
  return (
    <div className="oc-letter-text">
      {text.split("\n").map((line, i) => {
        const head = /^Reason \d+: rule (\S+)/.exec(line);
        const parts = line.startsWith("What you wrote:") ? [line] : line.split(new RegExp(PLACEHOLDER, "g"));
        return (
          <p key={i} id={head ? `oc-reason-${head[1]}` : undefined} className={head ? "oc-reason-head" : line ? undefined : "oc-gap"}>
            {parts.map((p, j) => (IS_PLACEHOLDER.test(p)
              ? <mark key={j} className="oc-ph" title="Fill this in before release">{p}</mark> : <span key={j}>{p}</span>))}
          </p>
        );
      })}
    </div>
  );
}

function OutcomeLetter({ letter, out, signedOff, onChanged, onRedraft }: {
  letter: Letter; out: Outcome; signedOff: boolean; onChanged: () => void; onRedraft?: () => Promise<unknown>;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [end, setEnd] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const released = Boolean(letter.released_at);
  const viewed = Boolean(letter.letter_viewed_at);
  const q = letter.quality_checks as unknown as Record<string, unknown>;

  const check = () => { const el = box.current; if (el && el.scrollTop + el.clientHeight >= el.scrollHeight - 8) setEnd(true); };
  useEffect(() => { setEnd(false); setTimeout(check, 0); }, [letter.id, letter.body_text]); // eslint-disable-line react-hooks/exhaustive-deps
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setFailure(null);
    try { await fn(); setEditing(false); onChanged(); } catch (e) { setFailure(e); } finally { setBusy(false); }
  };
  const markRead = () => { if (end && !viewed && !released) void act(() => api.letterViewed(letter.id)); };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.metaKey || e.ctrlKey || e.altKey || (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      if (e.key === "r" || e.key === "R") markRead();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const list = (key: string) => (Array.isArray(q[key]) ? (q[key] as string[]) : []);
  return (
    <section className="oc-letter" aria-label={KIND_TITLE[letter.kind ?? "decline"]}>
      <header className="oc-letter-head">
        <h2>{KIND_TITLE[letter.kind ?? "decline"]}</h2>
        <span className="oc-chips">
          <span className="oc-chip">Letter updated {timeOf(letter.updated_at ?? letter.created_at)}</span>
          {letter.status === "edited" && <span className="oc-chip edited"><Icon name="edit" size={14} />Edited by officer</span>}
          {letter.template && <span className="oc-chip">Built from your decisions by a fixed template (no AI)</span>}
          {released ? <span className="oc-chip ok"><Icon name="check" size={14} />Released {formatDate(letter.released_at, true)}</span>
            : viewed ? <span className="oc-chip ok"><Icon name="check" size={14} />You read it</span>
            : <span className="oc-chip wait"><Icon name="clock" size={14} />Not read to the end yet</span>}
        </span>
      </header>
      {out.letter_matches_findings === false && !released && (
        <div className="notice warn-notice"><Icon name="info" /><span>Your decisions changed after this letter was edited. It no longer lists exactly the rules you confirmed as Not met.
          {onRedraft && <> <button className="link-button" disabled={busy} onClick={() => void act(onRedraft)}>Redraft it from my decisions</button></>}</span></div>
      )}
      {editing
        ? <textarea className="oc-editor" rows={24} value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Letter text" />
        : <div className="oc-paper" ref={box} onScroll={check} tabIndex={0} aria-label="Letter text, scroll to read to the end"><LetterText text={letter.body_text} /></div>}
      <ErrorNotice error={failure} />
      {!released && (
        <div className="oc-letter-actions">
          {editing ? <>
            <Button icon="check" disabled={busy} onClick={() => void act(() => api.patchLetter(letter.id, { body_text: draft }))}>Save changes</Button>
            <Button variant="quiet" onClick={() => setEditing(false)}>Cancel</Button>
            <small className="muted">Saved text is checked again by code, and you read it again before release.</small>
          </> : <>
            <Button variant="secondary" disabled={busy || viewed || !end} onClick={markRead}
              title={end ? undefined : "Scroll to the end of the letter first"}>{viewed ? "Read to the end" : "I have read the letter"} <kbd>R</kbd></Button>
            {!signedOff && <Button variant="quiet" icon="edit" onClick={() => { setDraft(letter.body_text); setEditing(true); }}>Edit letter</Button>}
            {!signedOff && onRedraft && letter.status !== "edited" && <Button variant="quiet" disabled={busy} onClick={() => void act(onRedraft)}>Draft again from my decisions</Button>}
          </>}
        </div>
      )}
      <details className="oc-checks" open={!q.all_passed}>
        <summary>Letter checks (code) {q.all_passed ? <span className="oc-chip ok"><Icon name="check" size={14} />All pass</span> : <span className="oc-chip wait"><Icon name="info" size={14} />Something to fix</span>}</summary>
        <ul>
          <Check ok={q.quotes_match_application !== false} label="Quotes match the applicant’s own words" extra={list("unmatched_quotes")} />
          <Check ok={q.every_reason_cites_confirmed_rule !== false} label="Every reason is a rule you confirmed" extra={list("uncited_or_unconfirmed_rules")} />
          <Check ok={q.no_forbidden_terms !== false} label="No rank, score, comparison or other forbidden words" extra={list("forbidden_terms")} />
          <Check ok={q.no_style_criticism !== false} label="No comment on grammar, spelling or style" extra={list("style_criticism")} />
          <Check ok={q.numbers_from_allowed_sources !== false} label="Every number comes from the settings, the rule or the applicant’s words" extra={list("unknown_numbers")} />
          <Check ok={q.no_placeholders !== false} label="No [placeholder] left" extra={list("placeholders")} />
          {q.feedback_from_officer_reasons === false && <Check ok={false} label="Feedback only from the criteria you chose" extra={list("feedback_not_chosen")} />}
          <Check ok={q.reading_grade_ok !== false} warn label={`Reading level ${q.reading_grade ?? "–"} (aim for Year 9 or below)`} />
          {list("quotes_with_instructions").length > 0 && <Check ok={false} warn label="A quote contains instruction-like text. It is shown only as the applicant’s words." />}
        </ul>
      </details>
    </section>
  );
}

function Check({ ok, label, extra = [], warn = false }: { ok: boolean; label: string; extra?: string[]; warn?: boolean }) {
  return (
    <li className={ok ? "ok" : warn ? "warn" : "bad"}>
      <Icon name={ok ? "check" : warn ? "info" : "close"} size={14} /><span>{ok ? "" : warn ? "Check: " : "Fix: "}{label}{extra.length ? `: ${extra.join(", ")}` : ""}</span>
    </li>
  );
}

export default function Step4Outcome({ id, navigate, onChanged }: { id: string; navigate: Navigate; onChanged: () => void }) {
  const { data: out, error, reload: reloadOut } = useLoad(() => api.outcome(id), [id]);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [hold, setHold] = useState(false);
  const [chosen, setChosen] = useState<Set<string> | null>(null);
  const [reopening, setReopening] = useState(false);
  const [reason, setReason] = useState("");

  const reload = () => { reloadOut(); onChanged(); };
  const act = async (fn: () => Promise<unknown>) => { setBusy(true); setFailure(null); try { await fn(); reload(); } catch (e) { setFailure(e); } setBusy(false); };
  const cond = out?.conditions;
  const signedOff = Boolean(out?.signed_off);
  const signOff = () => { if (cond?.ready && !busy && !signedOff) void act(() => api.signoffRelease(id, !hold)); };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); signOff(); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  if (error) return <ErrorNotice error={error} />;
  if (!out) return <Loading />;

  const kind = out.letter_kind;
  const picked = chosen ?? new Set(out.feedback_chosen ?? []);
  const redraft = kind === "decline" ? () => api.generateLetter(id) : kind === "not_selected" ? () => api.notSelectedLetter(id, [...picked])
    : kind === "next_steps" ? () => api.nextStepsLetter(id) : undefined;
  const fixable = out.result === "decline" && out.unmet.length > 0 && out.unmet.every((u) => u.fixable_by_document);
  const status = out.result === "decline" ? "Not eligible" : out.round_decision?.decision === "not_selected_competitive" ? "Not selected"
    : out.round_decision?.decision === "selected" ? "Selected" : out.awaiting_round_decision ? "Eligible, waiting for the round decision"
    : out.result === "eligible" ? "Eligible" : out.result === "needs_information" ? "More information needed" : "Undecided";
  const scrollTo = (code: string) => document.getElementById(`oc-reason-${code}`)?.scrollIntoView({ behavior: "smooth", block: "start" });

  return (
    <div className="oc-page">
      <section className="panel oc-head">
        <div><p className="eyebrow">Step 4 of 4</p><h2>Outcome</h2>
          <p>{out.result === "decline" ? "You confirmed one or more eligibility rules as Not met. The letter lists each one."
            : out.awaiting_round_decision ? "Every eligibility rule is met and merit is marked. People decide who is funded in this round."
            : out.result === "eligible" ? "Every eligibility rule is met, or settled by you."
            : out.result === "needs_information" ? "Some rules still need more information from the applicant."
            : "Not every rule has your decision yet."}</p></div>
        <span className={`gf-chip ${out.result === "decline" ? "bad" : out.result === "eligible" ? "ok" : "wait"}`}>
          <Icon name={out.result === "decline" ? "close" : out.result === "eligible" ? "check" : "clock"} size={14} />{status}</span>
      </section>

      {(out.not_assessed?.length ?? 0) > 0 && (
        <p className="notice"><Icon name="lock" /><span>{out.not_assessed_label}: {out.not_assessed!.map((s) => ({ eligibility: "the remaining eligibility rules", consistency: "consistency", ai_use: "the AI-use check", merit: "merit" }[s] ?? s)).join(", ")}.</span></p>
      )}
      {fixable && !signedOff && (
        <div className="notice warn-notice" role="status"><Icon name="file" />
          <span>This could be fixed by sending a document. Do you want to ask for more information instead of declining?
            {" "}<button className="link-button" onClick={() => navigate({ name: "review", id, step: 1 })}>Draft a request for the document</button></span></div>
      )}
      {out.awaiting_round_decision && (
        <div className="notice" role="status"><Icon name="info" /><span>Record the panel’s decision in <a href="#/outcomes">Outcomes</a>. The panel decides; this tool only records it.</span></div>
      )}
      {out.result === "needs_information" && (
        <div className="notice"><Icon name="info" /><span>Go back to ask the applicant for what is missing: <button className="link-button" onClick={() => navigate({ name: "review", id, step: 1 })}>Step 1: Documents</button> or <button className="link-button" onClick={() => navigate({ name: "review", id, step: 3 })}>Step 3: Assessment</button>.</span></div>
      )}

      {kind && (
        <div className="oc-grid">
          {out.letter
            ? <OutcomeLetter letter={out.letter} out={out} signedOff={signedOff} onChanged={reload} onRedraft={signedOff ? undefined : redraft} />
            : <section className="oc-letter empty-state"><p>No letter has been drafted yet.</p>
                {redraft && <Button icon="file" disabled={busy} onClick={() => void act(redraft)}>Draft the letter</Button>}</section>}

          <aside className="oc-side">
            {kind === "decline" && (
              <section className="oc-card" aria-labelledby="oc-reasons">
                <h3 id="oc-reasons">Rules you confirmed as Not met</h3>
                <ol className="oc-reasons">
                  {out.unmet.map((u) => (
                    <li key={u.rule_code}>
                      <strong>{u.rule_code}</strong> {u.rule_text}
                      {u.quote ? <blockquote>“{u.quote}” <button className="link-button" onClick={() => navigate({ name: "trace", id, item: `rule:${u.rule_code}` })}>Show in document</button></blockquote>
                        : <p className="muted">Nothing in the application covers this.</p>}
                      <button className="oc-why" onClick={() => scrollTo(u.rule_code)} title="Show this paragraph in the letter"><b>Your reason:</b> {u.reason}</button>
                    </li>
                  ))}
                </ol>
              </section>
            )}
            {kind === "not_selected" && (
              <section className="oc-card" aria-labelledby="oc-feedback">
                <h3 id="oc-feedback">Feedback on this application (optional)</h3>
                <p className="muted">Your own marking reasons. Tick the ones to include; the letter only tidies spacing and punctuation.{out.include_marks_in_letter ? " This grant shows the applicant’s own mark for each criterion." : " Marks are not shown in letters."}</p>
                {(out.feedback_options ?? []).length === 0 ? <p className="muted">No marking reasons to offer.</p> : (
                  <fieldset className="oc-feedback">
                    <legend className="sr-only">Criteria to include</legend>
                    {out.feedback_options!.map((f) => (
                      <label key={f.rule_code}><input type="checkbox" disabled={signedOff} checked={picked.has(f.rule_code)}
                        onChange={(e) => { const n = new Set(picked); if (e.target.checked) n.add(f.rule_code); else n.delete(f.rule_code); setChosen(n); }} />
                        <span><b>{f.criterion}</b> {f.reason}</span></label>
                    ))}
                  </fieldset>
                )}
                {!signedOff && <Button variant="secondary" disabled={busy || !chosen} onClick={() => void act(async () => { await api.notSelectedLetter(id, [...picked]); setChosen(null); })}>Update the letter</Button>}
              </section>
            )}
            {out.round_decision && (
              <section className="oc-card" aria-labelledby="oc-round">
                <h3 id="oc-round">Round decision (recorded)</h3>
                <dl className="rv-facts">
                  <div><dt>Decision</dt><dd>{out.round_decision.decision === "selected" ? "Selected" : "Not selected (competitive)"}</dd></div>
                  <div><dt>Decided by</dt><dd>{out.round_decision.decided_by}, {out.round_decision.authority}</dd></div>
                  <div><dt>Date</dt><dd>{formatDate(out.round_decision.decided_at)}</dd></div>
                  <div><dt>Where recorded</dt><dd>{out.round_decision.reference}</dd></div>
                  {out.round_decision.reason_note && <div><dt>Reason note</dt><dd>{out.round_decision.reason_note}</dd></div>}
                </dl>
              </section>
            )}

            {!signedOff && cond && (
              <section className="oc-card oc-signoff" aria-labelledby="oc-signoff">
                <h3 id="oc-signoff">Before you sign off</h3>
                <ul className="oc-conds">
                  {cond.items.map((i) => <li key={i.key} className={i.ok ? "ok" : "todo"}><Icon name={i.ok ? "check" : "clock"} size={14} /><span>{i.ok ? "Done: " : "To do: "}{i.label}</span></li>)}
                </ul>
                <p className="statement">{out.statement}</p>
                {kind === "not_selected" && (
                  <label className="confirm-check"><input type="checkbox" checked={hold} onChange={(e) => setHold(e.target.checked)} />
                    <span>Sign off now, and release the letter later with the rest of the round</span></label>
                )}
                <ErrorNotice error={failure} />
                <Button disabled={!cond.ready || busy} onClick={signOff} title={cond.reason ?? undefined}>
                  {hold && kind === "not_selected" ? "Sign off (release later)" : "Sign off and release letter"} <kbd>Ctrl</kbd><kbd>Enter</kbd></Button>
                {!cond.ready && cond.reason && <small className="oc-why-not"><Icon name="info" size={14} />{cond.reason}</small>}
                <small className="muted">Each application is signed off on its own. No email is connected: a released letter is marked ready to send.</small>
              </section>
            )}
          </aside>
        </div>
      )}

      {signedOff && out.record && (
        <section className="panel gf-record">
          <h3>Final record</h3>
          <dl className="rv-facts">
            <div><dt>Outcome</dt><dd>{status}</dd></div>
            <div><dt>Sign-off version</dt><dd>{out.record.version}</dd></div>
            <div><dt>Officer</dt><dd>{out.record.officer}</dd></div>
            <div><dt>Signed</dt><dd>{formatDate(out.record.signed_at, true)}</dd></div>
            <div><dt>Letter</dt><dd>{out.letter ? `Version ${out.letter.version}, ${out.letter.released_at ? `released ${formatDate(out.letter.released_at, true)}` : "signed off, not released yet"}` : "None"}</dd></div>
          </dl>
          <div className="rv-table-wrap"><table className="rv-table">
            <thead><tr><th>Rule</th><th>Decision</th><th>Evidence</th></tr></thead>
            <tbody>{out.record.decisions.map((d) => (
              <tr key={d.rule_code}><td><strong>{d.rule_code}</strong> {d.rule_text.replace(/\s*Weight on the form: \d+%\.?/i, "")}</td>
                <td>{d.decision}{d.overridden ? " (you changed the AI’s suggestion)" : ""}{d.reason ? <small className="rv-reasonline">{d.reason}</small> : null}</td>
                <td><button className="link-button" onClick={() => navigate({ name: "trace", id, item: `rule:${d.rule_code}` })}>Open evidence <Icon name="arrow" size={14} /></button></td></tr>))}</tbody>
          </table></div>
          {out.record.reopened.length > 0 && <><h3>Earlier versions</h3><ul className="cx-list">{out.record.reopened.map((r) => <li key={r.version}>Version {r.version} was reconsidered {formatDate(r.reopened_at, true)}: {r.reason}</li>)}</ul></>}
          <ErrorNotice error={failure} />
          {!reopening ? <Button variant="secondary" onClick={() => setReopening(true)}>Start a reconsideration</Button> : (
            <div className="gf-inline">
              <label className="field"><span>Why is this outcome being reconsidered? <b>*</b></span><textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
              <Button disabled={busy || !reason.trim()} onClick={() => void act(async () => { await api.reopen(id, reason.trim()); setReopening(false); setReason(""); })}>Reconsider with this reason</Button>
              <Button variant="quiet" onClick={() => setReopening(false)}>Cancel</Button>
              <small className="muted">A released letter stays as it was. A new version follows the new decision.</small>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
