// Round decisions: record what PEOPLE decided about funding (Selected / Not selected, competitive). The tool computes nothing:
// no marks, totals, averages or order by merit. The list is by reference. Rows are chosen one by one; there is no "select all"
// and nothing is chosen by default. Saving more than one asks the officer to type the count. Saving releases nothing: it drafts
// letters for the officer to review in each application's Outcome step. Signed-off letters held for the round are released
// below, only those this officer opened, again after typing the count.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { api, type HeldLetter, type RoundDecision } from "../api";
import { usePersisted, usePersistedSet } from "../persisted";
import { Button, ErrorNotice, Icon, Loading, formatDate, useLoad } from "../ui";

const MIN_NOTE = 20;
const today = () => new Date().toISOString().slice(0, 10);

export default function RoundDecisions() {
  const { data, error, reload } = useLoad(api.roundDecisions, []);
  const held = useLoad(api.heldLetters, []);
  const [pickedSet, setPickedSet] = usePersistedSet("rounds.picked");
  const picked = pickedSet;
  const setPicked = (f: Set<string> | ((s: Set<string>) => Set<string>)) => setPickedSet(typeof f === "function" ? f(pickedSet) : f);
  const [decision, setDecision] = usePersisted<RoundDecision["decision"] | "">("rounds.decision", "");
  const [decidedBy, setDecidedBy] = usePersisted("rounds.by", "Assessment panel");
  const [authority, setAuthority] = usePersisted("rounds.authority", "");
  const [date, setDate] = usePersisted("rounds.date", today());
  const [reference, setReference] = usePersisted("rounds.ref", "");
  const [note, setNote] = usePersisted("rounds.note", "");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [saved, setSaved] = useState<string | null>(null);

  if (error) return <main className="page"><ErrorNotice error={error} /></main>;
  if (!data) return <main className="page"><Loading /></main>;

  const chosen = data.ready.filter((r) => picked.has(r.application_id));
  const missing = [
    !chosen.length && "Choose at least one application",
    !decision && "Choose the decision",
    !decidedBy.trim() && "Say who made the decision",
    !authority.trim() && "Say under what authority (panel or role)",
    !reference.trim() && "Give the panel minute or meeting reference",
    decision === "not_selected_competitive" && note.trim().length < MIN_NOTE && `Write a reason note (at least ${MIN_NOTE} characters)`,
  ].filter(Boolean) as string[];

  const save = async (typed: number | null) => {
    setBusy(true); setFailure(null);
    try {
      await api.recordRoundDecisions({ application_ids: chosen.map((r) => r.application_id), decision: decision as RoundDecision["decision"],
        decided_by: decidedBy.trim(), authority: authority.trim(), decided_at: date, reference: reference.trim(),
        reason_note: note.trim() || null, typed_count: typed });
      setSaved(`${chosen.length} ${chosen.length === 1 ? "decision" : "decisions"} recorded. Draft letters are ready in each application’s Outcome step. Nothing was sent.`);
      setPicked(new Set()); setReference(""); setNote(""); setConfirming(false); reload(); held.reload();
    } catch (e) { setFailure(e); setConfirming(false); } finally { setBusy(false); }
  };

  return (
    <main className="page rd">
      <div className="page-heading"><div><h1>Round decisions</h1>
        <p>Record the decision the assessment panel made. People decide who is funded; this screen only records it and drafts the letters.</p></div></div>
      <p className="notice" role="note"><Icon name="info" /><span><strong>No ranking is shown here.</strong> Applications are listed by reference number. Open an application to see its own marks and reasons.</span></p>
      {saved && <p className="notice ok-notice" role="status"><Icon name="check" />{saved}</p>}

      <section className="panel" aria-labelledby="rd-ready">
        <div className="panel-heading"><div><h2 id="rd-ready">Ready for a round decision <span className="pill">{data.ready.length}</span></h2>
          <p>Every eligibility rule confirmed Met, and every merit criterion marked with a reason (or Not assessed with a reason).</p></div></div>
        {data.ready.length === 0 ? <p className="empty-state">No application is ready for a round decision.</p> : (
          <div className="rv-table-wrap"><table className="rv-table">
            <thead><tr><th><span className="sr-only">Choose</span></th><th>Reference</th><th>Grant</th><th><span className="sr-only">Open</span></th></tr></thead>
            <tbody>{data.ready.map((r) => (
              <tr key={r.application_id}>
                <td><input type="checkbox" aria-label={`Choose ${r.reference}`} checked={picked.has(r.application_id)}
                  onChange={(e) => setPicked((s) => { const n = new Set(s); if (e.target.checked) n.add(r.application_id); else n.delete(r.application_id); return n; })} /></td>
                <td><strong>{r.reference}</strong></td><td>{r.program_name}</td>
                <td><a className="button button-quiet" href={r.deep_link}>Open application</a></td>
              </tr>))}</tbody>
          </table></div>
        )}
      </section>

      {data.ready.length > 0 && (
        <section className="panel rd-form" aria-labelledby="rd-form">
          <div className="panel-heading"><div><h2 id="rd-form">Record the decision for {chosen.length || "the chosen"} {chosen.length === 1 ? "application" : "applications"}</h2></div></div>
          <fieldset className="rd-decision"><legend>Decision</legend>
            <label><input type="radio" name="rd-decision" checked={decision === "selected"} onChange={() => setDecision("selected")} /> Selected</label>
            <label><input type="radio" name="rd-decision" checked={decision === "not_selected_competitive"} onChange={() => setDecision("not_selected_competitive")} /> Not selected (competitive)</label>
          </fieldset>
          <div className="rd-fields">
            <label className="field"><span>Decided by <b>*</b></span><input value={decidedBy} onChange={(e) => setDecidedBy(e.target.value)} placeholder="Assessment panel" /></label>
            <label className="field"><span>Authority (panel or role) <b>*</b></span><input value={authority} onChange={(e) => setAuthority(e.target.value)} placeholder="For example: Panel chair" /></label>
            <label className="field"><span>Date of the decision <b>*</b></span><input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
            <label className="field"><span>Where it was decided (minute or meeting reference) <b>*</b></span><input value={reference} onChange={(e) => setReference(e.target.value)} placeholder="For example: Panel minute 2026/7" /></label>
          </div>
          {decision === "not_selected_competitive" && (
            <label className="field"><span>Reason note <b>*</b> <small>(at least {MIN_NOTE} characters; for the record, not a comparison)</small></span>
              <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="For example: Funding was limited to the places available; this application was not among those the panel recommended." /></label>
          )}
          <ErrorNotice error={failure} />
          {missing.length > 0 && <ul className="rd-missing">{missing.map((m) => <li key={m}><Icon name="clock" size={14} />{m}</li>)}</ul>}
          <Button disabled={missing.length > 0 || busy} onClick={() => (chosen.length > 1 ? setConfirming(true) : void save(null))}>Save decision</Button>
          <small className="muted">Saving does not release anything. It drafts letters and moves the applications to Outcome.</small>
        </section>
      )}

      <details className="panel rd-later">
        <summary><h2>Not ready yet <span className="pill">{data.not_ready.length}</span></h2></summary>
        {data.not_ready.length === 0 ? <p className="empty-state">Nothing here.</p> : (
          <ul className="rd-why">{data.not_ready.map((r) => (
            <li key={r.application_id}><strong>{r.reference}</strong> <span>{(r.why ?? []).join("; ")}</span> <a href={r.deep_link}>Open</a></li>))}</ul>
        )}
      </details>

      <section className="panel" aria-labelledby="rd-decided">
        <div className="panel-heading"><div><h2 id="rd-decided">Decisions recorded <span className="pill">{data.decided.length}</span></h2></div></div>
        {data.decided.length === 0 ? <p className="empty-state">No decisions recorded yet.</p> : (
          <div className="rv-table-wrap"><table className="rv-table">
            <thead><tr><th>Reference</th><th>Decision</th><th>Date</th><th>Where recorded</th><th>Outcome</th></tr></thead>
            <tbody>{data.decided.map((r) => (
              <tr key={r.application_id}><td><strong>{r.reference}</strong></td>
                <td>{r.decision === "selected" ? "Selected" : "Not selected (competitive)"}</td><td>{formatDate(r.decided_at)}</td><td>{r.reference_minute}</td>
                <td><a href={r.deep_link}>{r.signed_off ? "Signed off" : "Open Outcome"}</a></td></tr>))}</tbody>
          </table></div>
        )}
      </section>

      <BatchRelease held={held.data} onDone={() => { held.reload(); reload(); }} />

      {confirming && (
        <TypeCount title="Confirm the decision" count={chosen.length} busy={busy} onCancel={() => setConfirming(false)} onConfirm={(n) => void save(n)}
          action={`Record “${decision === "selected" ? "Selected" : "Not selected (competitive)"}” for these applications`}>
          <ul className="rd-refs">{chosen.map((r) => <li key={r.application_id}>{r.reference}</li>)}</ul>
        </TypeCount>
      )}
    </main>
  );
}

function BatchRelease({ held, onDone }: { held: HeldLetter[] | null; onDone: () => void }) {
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  if (!held) return null;
  const chosen = held.filter((l) => picked.has(l.letter_id));
  const release = async (n: number) => {
    setBusy(true); setFailure(null);
    try { await api.releaseLetters(chosen.map((l) => l.letter_id), n); setPicked(new Set()); onDone(); } catch (e) { setFailure(e); }
    finally { setBusy(false); setConfirming(false); }
  };
  return (
    <section className="panel" aria-labelledby="rd-release">
      <div className="panel-heading"><div><h2 id="rd-release">Signed-off letters to release <span className="pill">{held.length}</span></h2>
        <p>Only letters you opened and read yourself can be released. No email is connected: a released letter is marked ready to send.</p></div></div>
      {held.length === 0 ? <p className="empty-state">No signed-off letters are waiting.</p> : (
        <div className="rv-table-wrap"><table className="rv-table">
          <thead><tr><th><span className="sr-only">Choose</span></th><th>Reference</th><th>Letter</th><th>You read it</th></tr></thead>
          <tbody>{held.map((l) => (
            <tr key={l.letter_id}>
              <td><input type="checkbox" aria-label={`Choose the letter for ${l.reference}`} disabled={!l.viewed_by_me} checked={picked.has(l.letter_id)}
                onChange={(e) => setPicked((s) => { const n = new Set(s); if (e.target.checked) n.add(l.letter_id); else n.delete(l.letter_id); return n; })} /></td>
              <td><strong>{l.reference}</strong></td><td>{l.kind === "not_selected" ? "Not selected" : l.kind === "decline" ? "Not eligible" : "Next steps"}</td>
              <td>{l.viewed_by_me ? <><Icon name="check" size={14} /> Yes</> : <a href={`#/applications/${l.application_id}?step=4`}>Open and read it first</a>}</td>
            </tr>))}</tbody>
        </table></div>
      )}
      <ErrorNotice error={failure} />
      {held.length > 0 && <Button disabled={!chosen.length || busy} onClick={() => setConfirming(true)}>Release {chosen.length || ""} {chosen.length === 1 ? "letter" : "letters"}</Button>}
      {confirming && (
        <TypeCount title="Release letters" count={chosen.length} busy={busy} onCancel={() => setConfirming(false)} onConfirm={(n) => void release(n)}
          action="Release the letters for">
          <ul className="rd-refs">{chosen.map((l) => <li key={l.letter_id}>{l.reference}</li>)}</ul>
        </TypeCount>
      )}
    </section>
  );
}

function TypeCount({ title, count, action, busy, onCancel, onConfirm, children }: {
  title: string; count: number; action: string; busy: boolean; onCancel: () => void; onConfirm: (n: number) => void; children: ReactNode;
}) {
  const [typed, setTyped] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const back = document.activeElement as HTMLElement | null;
    input.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); back?.focus(); };
  }, [onCancel]);
  return createPortal(
    <div className="modal-backdrop">
      <div className="db-dialog" role="dialog" aria-modal="true" aria-labelledby="rd-confirm-title">
        <div className="db-dialog-head"><h2 id="rd-confirm-title">{title}</h2></div>
        <p>{action} these {count} {count === 1 ? "application" : "applications"}:</p>
        {children}
        <label className="field"><span>Type the number of applications ({count}) to confirm</span>
          <input ref={input} inputMode="numeric" value={typed} onChange={(e) => setTyped(e.target.value)} aria-label={`Type ${count} to confirm`} /></label>
        <div className="rd-dialog-actions">
          <Button disabled={busy || typed.trim() !== String(count)} onClick={() => onConfirm(Number(typed))}>Confirm</Button>
          <Button variant="quiet" onClick={onCancel}>Cancel</Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
