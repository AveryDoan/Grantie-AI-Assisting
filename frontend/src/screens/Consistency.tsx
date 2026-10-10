// Consistency flags: places where an application's story may not add up.
// Signals for the officer to check, never findings or verdicts. A flag never changes a rule result and never
// blocks sign-off. Each flag is confirmed or dismissed by the officer; a dismissal needs a note.
// The table lives on the review screen; this file holds one flag's evidence and its Confirm / Dismiss actions.
import { useState } from "react";
import { createPortal } from "react-dom";
import { api, type ConsistencyFlag, type FlagEvidence } from "../api";
import { Button, ErrorNotice, Icon } from "../ui";

function Quote({ e }: { e: FlagEvidence }) {
  return (
    <figure className="cx-quote">
      <figcaption>{e.label}</figcaption>
      <blockquote>{e.restored ?? e.quote}</blockquote>
      {e.verified === false && <small className="cx-unverified"><Icon name="close" size={13} />Not found in the text</small>}
    </figure>
  );
}

export function Evidence({ items }: { items: FlagEvidence[] }) {
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

export function DismissDialog({ flag, close, done }: { flag: ConsistencyFlag; close: () => void; done: () => void }) {
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

/** One flag inside the side panel: what doesn't fit, the evidence, and the officer's decision. */
export function FlagDetail({ flag, onChanged, locked }: { flag: ConsistencyFlag; onChanged: () => void; locked?: boolean }) {
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
      <p className="cx-description">{flag.description}</p>
      <Evidence items={flag.evidence} />
      {flag.verification === "unclear" && <p className="muted">The AI reported a possible conflict but code could not find its quotes, so nothing is shown as a finding. Please review manually.</p>}
      {flag.note && <p className="cx-note"><strong>Your note:</strong> {flag.note}</p>}
      <ErrorNotice error={error} />
      {flag.verification === "verified" && !locked && (
        <div className="rule-actions">
          <Button icon="check" variant={decided ? "secondary" : "primary"} disabled={busy || flag.status === "confirmed"} onClick={() => void confirm()}>
            {flag.status === "confirmed" ? "Confirmed: needs follow-up" : "Confirm: needs follow-up"}
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
