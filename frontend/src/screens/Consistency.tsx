// Consistency flags: places where an application's story may not add up.
// Signals for the officer to check, never findings or verdicts. A flag never changes a rule result and never
// blocks sign-off. Each flag is confirmed or dismissed by the officer; a dismissal needs a note.
import { useState } from "react";
import { createPortal } from "react-dom";
import { api, type ConsistencyFlag, type ConsistencyView, type FlagEvidence } from "../api";
import { Button, ErrorNotice, Icon } from "../ui";

const GROUPS: { type: ConsistencyFlag["check_type"]; title: string; hint: string }[] = [
  { type: "cross_document", title: "Across documents", hint: "The form, the CoE, the arrival evidence and the referee letters, side by side." },
  { type: "timeline", title: "Timeline", hint: "Dates and roles, in order. The AI listed the events; code checked them." },
  { type: "narrative", title: "Statements that conflict", hint: "Two passages that appear to disagree. The AI pointed at them; code found both." },
  { type: "cross_application", title: "Across applications", hint: "Shared contacts, referees or wording with other applications. Only the shared attribute is named." },
  { type: "document_integrity", title: "Document signals", hint: "Weak signals from file details. Scans, re-saves and edits all change them." },
];

function Quote({ e }: { e: FlagEvidence }) {
  return (
    <figure className="cx-quote">
      <figcaption>{e.label}</figcaption>
      <blockquote>{e.restored ?? e.quote}</blockquote>
      {e.verified === false && <small className="cx-unverified"><Icon name="close" size={13} />Not found in the text</small>}
    </figure>
  );
}

function Evidence({ items }: { items: FlagEvidence[] }) {
  const quotes = items.filter((e) => e.kind === "quote");
  const fields = items.filter((e) => e.kind === "field");
  const signals = items.filter((e) => e.kind === "signal");
  const links = items.filter((e) => e.kind === "link");
  return (
    <>
      {(quotes.length > 0 || fields.length > 0) && (
        <div className="cx-sides">
          {[...fields.map((e) => (
            <figure className="cx-quote field" key={e.label}>
              <figcaption>{e.label}</figcaption>
              <blockquote>{e.value ?? "(value not shown)"}</blockquote>
            </figure>
          )), ...quotes.map((e, i) => <Quote key={`${e.label}-${i}`} e={e} />)]}
        </div>
      )}
      {signals.length > 0 && <ul className="cx-list">{signals.map((e) => <li key={e.label}>{e.label}</li>)}</ul>}
      {links.length > 0 && <ul className="cx-list">{links.map((e) => <li key={e.label}>{e.label}</li>)}</ul>}
    </>
  );
}

function DismissDialog({ flag, close, done }: { flag: ConsistencyFlag; close: () => void; done: () => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const save = async () => {
    setBusy(true); setError(null);
    try { await api.reviewFlag(flag.id, { action: "dismiss", note: note.trim() }); done(); } catch (e) { setError(e); setBusy(false); }
  };
  // Rendered at the top of the page (not inside the card), so the card's own styling cannot clip or blur it.
  return createPortal(
    <div className="modal-backdrop" role="presentation">
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="dismiss-title">
        <div className="dialog-head">
          <div><p className="eyebrow">{flag.type_label}</p><h2 id="dismiss-title">Dismiss this flag</h2></div>
          <button className="icon-button" onClick={close} aria-label="Close dialog"><Icon name="close" /></button>
        </div>
        <p>{flag.description}</p>
        <label className="field">
          <span>Why does this not need action? <b>*</b></span>
          <textarea rows={4} value={note} onChange={(e) => setNote(e.target.value)} placeholder="For example: the provider agreed a late arrival in writing." />
          <small>Your note is saved in the audit trail. Do not paste personal details such as phone numbers or addresses.</small>
        </label>
        <ErrorNotice error={error} />
        <div className="dialog-actions">
          <Button variant="secondary" onClick={close}>Cancel</Button>
          <Button disabled={busy || note.trim().length < 3} onClick={() => void save()}>Dismiss flag</Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function FlagCard({ flag, onChanged }: { flag: ConsistencyFlag; onChanged: () => void }) {
  const [dialog, setDialog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const decided = flag.status !== "open";
  const confirm = async () => {
    setBusy(true); setError(null);
    try { await api.reviewFlag(flag.id, { action: "confirm" }); onChanged(); } catch (e) { setError(e); setBusy(false); }
  };
  return (
    <article className={`cx-card ${flag.strength} ${decided ? "decided" : ""} ${flag.verification}`}>
      <header>
        <span className={`cx-strength ${flag.strength}`}>{flag.verification === "unclear" ? "Unclear" : flag.strength === "weak" ? "Weak signal" : "To check"}</span>
        <code className="cx-check">{flag.check_id}</code>
        {decided && <span className="cx-decision">{flag.status === "confirmed" ? "Confirmed by you" : "Dismissed by you"}</span>}
      </header>
      <p className="cx-description">{flag.description}</p>
      <Evidence items={flag.evidence} />
      {flag.note && <p className="cx-note"><strong>Your note:</strong> {flag.note}</p>}
      <ErrorNotice error={error} />
      {flag.verification === "verified" && (
        <div className="rule-actions">
          <Button icon="check" variant={decided ? "secondary" : "primary"} disabled={busy || flag.status === "confirmed"} onClick={() => void confirm()}>
            {flag.status === "confirmed" ? "Confirmed" : "Confirm: this needs follow-up"}
          </Button>
          <Button variant="secondary" icon="close" disabled={busy || flag.status === "dismissed"} onClick={() => setDialog(true)}>
            {flag.status === "dismissed" ? "Dismissed" : "Dismiss"}
          </Button>
        </div>
      )}
      {dialog && <DismissDialog flag={flag} close={() => setDialog(false)} done={() => { setDialog(false); onChanged(); }} />}
    </article>
  );
}

export function ConsistencyPanel({ view, onChanged, onTrace }: { view: ConsistencyView; onChanged: () => void; onTrace?: () => void }) {
  if (!view.enabled) return null;
  const verified = view.flags.filter((f) => f.verification === "verified");
  const unclear = view.flags.filter((f) => f.verification === "unclear");
  const open = verified.filter((f) => f.status === "open").length;
  return (
    <section className="panel consistency-panel" aria-labelledby="consistency-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Consistency flags · signals, not findings</p>
          <h2 id="consistency-title">Does the story add up?</h2>
          <p>Places where two pieces of the application do not fit together. Each is for you to check and confirm or dismiss.
            A flag does not change any rule result and does not stop sign-off.</p>
        </div>
        <div className="cx-summary">
          <strong>{open}</strong><span>{open === 1 ? "flag" : "flags"} to check</span>
          {onTrace && <button className="link-button" onClick={onTrace}>See what the AI read <Icon name="arrow" size={14} /></button>}
        </div>
      </div>
      {view.flags.length === 0 && <p className="empty-state">No consistency flags for this application.</p>}
      {GROUPS.map((g) => {
        const items = verified.filter((f) => f.check_type === g.type).sort((a, b) => (a.strength === b.strength ? 0 : a.strength === "strong" ? -1 : 1));
        if (!items.length) return null;
        return (
          <div className="cx-group" key={g.type}>
            <h3>{g.title} <small>{items.length}</small></h3>
            <p className="muted">{g.hint}</p>
            {items.map((f) => <FlagCard key={f.id} flag={f} onChanged={onChanged} />)}
          </div>
        );
      })}
      {unclear.length > 0 && (
        <div className="cx-group">
          <h3>Unclear <small>{unclear.length}</small></h3>
          <p className="muted">The AI reported a possible conflict but code could not find its quotes, so nothing is shown as a finding.</p>
          {unclear.map((f) => <FlagCard key={f.id} flag={f} onChanged={onChanged} />)}
        </div>
      )}
    </section>
  );
}
