// The email composer: ONE component for every "Ask the applicant for more" (document review, Documents header, eligibility findings).
// Nothing is sent until the officer chooses Send (or Mark as sent, after sending it themselves). The AI drafts from the ticked items
// using placeholders only; if it is unavailable the body is a marked template. Unresolved [placeholders] block sending.
// Keyboard: Ctrl+Enter sends only after the officer opened the preview or edited the text; Esc asks before discarding changes.
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, type ComposerDraft, type ComposerItem, type ComposerOptions } from "../api";
import { Button, ErrorNotice, Icon } from "../ui";

const PLACEHOLDER = /\[[^[\]\n]{2,60}\]/g;

export default function Composer({ appId, slots = [], note = "", prepare, onClose, onDone }: {
  appId: string; slots?: string[]; note?: string; prepare?: () => Promise<unknown>; onClose: () => void; onDone: (message: string) => void;
}) {
  const [opts, setOpts] = useState<ComposerOptions | null>(null);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [custom, setCustom] = useState<string[]>([]);
  const [customText, setCustomText] = useState("");
  const [officerNote, setOfficerNote] = useState(note);
  const [due, setDue] = useState("");
  const [subject, setSubject] = useState("");
  const [cc, setCc] = useState("");
  const [body, setBody] = useState("");
  const [draft, setDraft] = useState<ComposerDraft | null>(null);
  const [edited, setEdited] = useState(false);
  const [preview, setPreview] = useState(false);
  const [seen, setSeen] = useState(false);          // opened the preview or edited: needed before Ctrl+Enter
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [copied, setCopied] = useState(false);
  const [asking, setAsking] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  const redraft = async (o: ComposerOptions, t: Set<string>, c: string[], n: string, d: string) => {
    const picked = o.items.filter((i) => t.has(i.key)).map((i) => (i.slot ? { slot: i.slot } : { finding_id: i.finding_id }));
    if (!picked.length && !c.length) { setBody(""); setDraft(null); return; }
    const out = await api.composerDraft(appId, { picked, custom: c, note: n || null, due_date: d || null });
    setDraft(out); setBody(out.body); setSubject(out.subject); setEdited(false);
  };

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        if (prepare) await prepare();
        const o = await api.composerOptions(appId, slots);
        if (!live) return;
        const t = new Set(o.items.filter((i) => i.ticked).map((i) => i.key));
        setOpts(o); setTicked(t); setDue(o.due_date); setSubject(o.subject);
        await redraft(o, t, [], officerNote, o.due_date);
      } catch (e) { if (live) setError(e); } finally { if (live) setBusy(false); }
    })();
    return () => { live = false; };
  }, [appId]); // eslint-disable-line react-hooks/exhaustive-deps

  const placeholders = useMemo(() => [...new Set((body + " " + subject).match(PLACEHOLDER) ?? [])], [body, subject]);
  const noEmail = !opts?.to;
  const blocked = [placeholders.length ? `Fill in ${placeholders.join(", ")}` : "", noEmail ? "The applicant has no email address on record" : "", !body.trim() ? "Tick at least one item" : ""].filter(Boolean);
  const payload = (mode?: "send" | "marked_sent") => ({ subject, body, cc: cc || null, items: draft?.items ?? [], due_date: due || null, request_id: null, template: draft?.template ?? false, mode });
  const run = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true); setError(null);
    try { await fn(); onDone(message); } catch (e) { setError(e); setBusy(false); }
  };
  const toggle = (k: string) => { const t = new Set(ticked); if (t.has(k)) t.delete(k); else t.add(k); setTicked(t); };
  const change = (fn: () => void) => { if (edited && !confirmRedraft()) return; fn(); };
  const confirmRedraft = () => window.confirm("Redrafting replaces the text you edited. Continue?");
  const mailto = `mailto:${opts?.to ?? ""}?${new URLSearchParams({ subject, body, ...(cc ? { cc } : {}) }).toString().replace(/\+/g, "%20")}`;
  const sendNow = () => { if (!blocked.length && !busy) void run(() => api.composerSend(appId, payload(opts?.email_connected ? "send" : "marked_sent")), opts?.email_connected ? "Email sent. The application is waiting for the applicant." : "Marked as sent. The application is waiting for the applicant."); };
  const close = () => { if (edited || seen && body) setAsking(true); else onClose(); };

  useEffect(() => {
    const back = document.activeElement as HTMLElement | null;
    document.body.style.overflow = "hidden";
    box.current?.focus();
    return () => { document.body.style.overflow = ""; back?.focus(); };
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); close(); }
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && (preview || edited)) { e.preventDefault(); sendNow(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });

  const reasonCls = (r: string) => (r === "Needs evidence" ? "st-needs" : r === "Wrong document type" ? "st-notmet" : "st-judgement");
  return createPortal(
    <div className="modal-backdrop cmp-backdrop">
      <div className="cmp" role="dialog" aria-modal="true" aria-labelledby="cmp-title" ref={box} tabIndex={-1}>
        <header className="cmp-head">
          <div><p className="eyebrow">Ask the applicant for more</p><h2 id="cmp-title">Email to the applicant</h2></div>
          <button className="icon-button" onClick={close} aria-label="Close"><Icon name="close" /></button>
        </header>
        <div className="cmp-body">
          <div className="cmp-main">
            <dl className="cmp-fields">
              <label className="field"><span>To</span><input value={opts?.to ?? ""} readOnly aria-label="To" placeholder="No email on record" /></label>
              <label className="field"><span>Cc (optional)</span><input value={cc} onChange={(e) => { setCc(e.target.value); setSeen(true); }} /></label>
              <label className="field cmp-wide"><span>Subject</span><input value={subject} onChange={(e) => { setSubject(e.target.value); setEdited(true); }} /></label>
            </dl>
            <div className="cmp-bar">
              {draft?.template && <span className="cmp-chip warn"><Icon name="info" size={14} />{draft.template_note ?? "Template: AI unavailable"}</span>}
              <button className="link-button" aria-pressed={preview} onClick={() => { setPreview((v) => !v); setSeen(true); }}>{preview ? "Edit" : "Preview"}</button>
            </div>
            {preview
              ? <article className="cmp-preview" aria-label="Preview of the email"><p><b>To:</b> {opts?.to}</p><p><b>Subject:</b> {subject}</p><hr />
                  {body.split("\n").map((l, i) => <p key={i}>{l || " "}</p>)}</article>
              : <label className="field"><span>Message</span>
                  <textarea className="cmp-text" rows={16} value={body} onChange={(e) => { setBody(e.target.value); setEdited(true); setSeen(true); }} aria-label="Message" /></label>}
            {placeholders.length > 0 && <p className="cmp-warn" role="alert"><Icon name="info" size={14} />Fill in before sending: {placeholders.map((p) => <mark key={p} className="oc-ph">{p}</mark>)}</p>}
            <ErrorNotice error={error} />
          </div>
          <aside className="cmp-side" aria-label="Items requested">
            <h3>Items requested</h3>
            {!opts ? <p className="muted">Loading…</p> : (
              <ul className="cmp-items">
                {opts.items.map((i: ComposerItem) => (
                  <li key={i.key}><label><input type="checkbox" checked={ticked.has(i.key)} onChange={() => change(() => toggle(i.key))} />
                    <span><b>{i.label}</b> <span className={`cmp-chip ${reasonCls(i.reason)}`}>{i.reason}</span><small>{i.detail}</small></span></label></li>
                ))}
                {custom.map((c, n) => <li key={c + n}><label><input type="checkbox" checked readOnly /><span><b>Other</b><small>{c}</small></span></label>
                  <button className="link-button" onClick={() => change(() => setCustom(custom.filter((_, j) => j !== n)))}>Remove</button></li>)}
              </ul>
            )}
            <div className="cmp-add"><input value={customText} onChange={(e) => setCustomText(e.target.value)} placeholder="Add another item (free text)" aria-label="Add another item" />
              <Button variant="secondary" disabled={!customText.trim()} onClick={() => change(() => { setCustom([...custom, customText.trim()]); setCustomText(""); })}>Add</Button></div>
            <label className="field"><span>Your note (optional)</span><textarea rows={3} value={officerNote} onChange={(e) => setOfficerNote(e.target.value)} /></label>
            <label className="field"><span>Due date</span><input type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>
            <Button variant="secondary" disabled={!opts || busy} onClick={() => opts && void (async () => { setBusy(true); try { await redraft(opts, ticked, custom, officerNote, due); } catch (e) { setError(e); } setBusy(false); })()}>Redraft from these items</Button>
            <small className="muted">The draft is written without the applicant’s name, email or other personal details. The server adds the name back.</small>
          </aside>
        </div>
        <footer className="cmp-foot">
          {blocked.length > 0 && <small className="cmp-why"><Icon name="info" size={14} />{blocked[0]}</small>}
          {!opts?.email_connected && (
            <small className="muted cmp-explain">No email service is connected. <b>Copy email</b> puts it on your clipboard. <b>Open in my email app</b> starts a message you send yourself. <b>Mark as sent</b> records that you sent it.</small>
          )}
          <div className="cmp-actions">
            <Button variant="quiet" onClick={close}>Cancel</Button>
            <Button variant="secondary" disabled={busy || !body.trim()} onClick={() => void run(() => api.composerSave(appId, payload()), "Draft saved. Nothing was sent.")}>Save draft</Button>
            {!opts?.email_connected && <>
              <Button variant="secondary" icon="file" disabled={!body.trim()} onClick={() => { void navigator.clipboard?.writeText(`To: ${opts?.to}\nSubject: ${subject}\n\n${body}`).then(() => setCopied(true)); }}>{copied ? "Copied" : "Copy email"}</Button>
              <a className={`button button-secondary ${!body.trim() ? "disabled" : ""}`} href={body.trim() ? mailto : undefined} onClick={() => setSeen(true)}><Icon name="mail" />Open in my email app</a>
            </>}
            <Button icon="send" disabled={busy || blocked.length > 0} onClick={sendNow} title={blocked[0]}>{opts?.email_connected ? "Send email" : "Mark as sent"} <kbd>Ctrl</kbd><kbd>Enter</kbd></Button>
          </div>
        </footer>
        {asking && (
          <div className="cmp-ask" role="alertdialog" aria-label="Discard changes?"><div>
            <p><b>Discard this email?</b> Your changes are not saved and nothing has been sent.</p>
            <Button variant="quiet" onClick={() => setAsking(false)}>Keep editing</Button><Button variant="secondary" onClick={onClose}>Discard</Button></div></div>
        )}
      </div>
    </div>,
    document.body,
  );
}
