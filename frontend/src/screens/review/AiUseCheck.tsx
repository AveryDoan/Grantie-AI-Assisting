// Sub-step 3.3, AI-use check. A weak signal from a local detector, run only when the officer asks. It decides nothing: it
// never sets a status, never blocks sign-off and never reaches merit, letters or the panel summary. No percentages, no red,
// no accusing words, no sentence highlighting. The only follow-up is a neutral request to explain an answer in one's own words.
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, type AiUseField, type AiUseView, type Detail } from "../../api";
import { Button, ErrorNotice, Icon, formatDate } from "../../ui";

export const AI_USE_CAVEAT = "This check is a weak signal. It was trained on older AI text and cannot tell allowed use (spelling, grammar, organising ideas) from disallowed use (writing, improving, paraphrasing). People who write plainly or in a second language are often marked 'AI-like' by tools like this. It is a prompt for a conversation, not evidence.";

function ResultChip({ result }: { result: AiUseField["result"] }) {
  return <span className="ai-chip">{result ?? "Not checked yet"}</span>;
}

export function AiUseCheck({ detail, locked, onChanged }: { detail: Detail; locked: boolean; onChanged: () => void }) {
  const [view, setView] = useState<AiUseView | null>(detail.ai_use ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState<AiUseField | null>(null);
  useEffect(() => setView(detail.ai_use ?? null), [detail.ai_use]);
  if (!view) return null;

  const run = async () => {
    setBusy(true); setError(null);
    try { setView(await api.runAiUseCheck(detail.application.id)); onChanged(); } catch (e) { setError(e); }
    setBusy(false);
  };
  const ran = view.fields.some((f) => f.result);

  return (
    <section className="rv-section ai-use" aria-label="AI-use check">
      {view.preview && (
        <p className="ai-preview" role="note"><Icon name="info" size={15} />Demo preview. This check has not passed its fairness test, so it is off for real
          applications. It is shown here on the synthetic demo applicant only, to demonstrate how it would work.</p>
      )}
      <article className="ai-info">
        <h3>The form's rule on AI use</h3>
        <blockquote>{view.policy}</blockquote>
        <p>{AI_USE_CAVEAT}</p>
        <p className="ai-ack"><Icon name={view.acknowledged ? "check" : "info"} size={15} />
          Applicant's acknowledgement, "I have followed these rules.": {view.acknowledged ? `given${view.acknowledged_at ? ` on ${formatDate(view.acknowledged_at)}` : ""}` : "not recorded"}</p>
      </article>
      <div className="ai-run">
        <Button variant="secondary" icon="search" disabled={busy || locked || !view.enabled} onClick={() => void run()}>{busy ? "Checking on this computer…" : ran ? "Run AI-use check again" : "Run AI-use check"}</Button>
        <small>Runs on this server only. Answers under {view.min_words} words are not checked.</small>
      </div>
      <ErrorNotice error={error} />
      <div className="rv-table-wrap" role="region" aria-label="AI-use check results" tabIndex={-1}>
        <table className="rv-table">
          <thead><tr><th>Field</th><th>Words</th><th>Result</th><th>Action</th></tr></thead>
          <tbody>
            {view.fields.map((f) => (
              <tr key={f.field} className="rv-row" tabIndex={0} data-row onClick={() => setOpen(f)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(f); } }} aria-label={`${f.label}. ${f.result ?? "Not checked yet"}. Open`}>
                <td><strong>{f.label}</strong></td>
                <td>{f.words}</td>
                <td><ResultChip result={f.result} /></td>
                <td>{f.action ? f.action.label : <span className="rv-muted">–</span>}</td>
              </tr>
            ))}
            {view.fields.length === 0 && <tr><td colSpan={4} className="empty-state">No answer fields are set up for this check.</td></tr>}
          </tbody>
        </table>
      </div>
      {open && <FieldDrawer field={view.fields.find((x) => x.field === open.field) ?? open} view={view} appId={detail.application.id} locked={locked}
        onClose={() => setOpen(null)} onDone={(v) => { setView(v); onChanged(); }} />}
    </section>
  );
}

function FieldDrawer({ field, view, appId, locked, onClose, onDone }: {
  field: AiUseField; view: AiUseView; appId: string; locked: boolean; onClose: () => void; onDone: (v: AiUseView) => void;
}) {
  const [note, setNote] = useState(field.action?.note ?? "");
  const [writing, setWriting] = useState(false);
  const [draft, setDraft] = useState<{ id: string; message_text: string } | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const head = useRef<HTMLHeadingElement>(null);
  useEffect(() => { head.current?.focus(); const k = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopImmediatePropagation(); onClose(); } }; window.addEventListener("keydown", k, true); return () => window.removeEventListener("keydown", k, true); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (action: "no_action" | "note" | "ask") => {
    setBusy(true); setError(null);
    try {
      const out = await api.aiUseAction(appId, { field: field.field, action, note: action === "note" ? note.trim() : undefined });
      onDone(out.check);
      if (out.draft) { setDraft(out.draft); setMessage(out.draft.message_text); } else onClose();
    } catch (e) { setError(e); }
    setBusy(false);
  };
  const send = async () => {
    if (!draft) return;
    setBusy(true); setError(null);
    try { await api.editRequest(draft.id, message.trim()); await api.sendRequest(draft.id); onClose(); } catch (e) { setError(e); setBusy(false); }
  };

  return createPortal(
    <div className="modal-backdrop" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="dialog wide ai-drawer" role="dialog" aria-modal="true" aria-labelledby="ai-field-title">
        <div className="dialog-head">
          <div><p className="eyebrow">AI-use check · weak signal only</p><h2 id="ai-field-title" ref={head} tabIndex={-1}>{field.label}</h2></div>
          <button className="icon-button" onClick={onClose} aria-label="Close (Esc)"><Icon name="close" /></button>
        </div>
        {!draft ? (
          <>
            <p><strong>Result:</strong> <ResultChip result={field.result} /> <span className="rv-muted">({field.words} words)</span></p>
            <blockquote className="ai-text">{field.text || "No answer was given."}</blockquote>
            <p className="rv-muted">Model: {view.model ?? "not run yet"}{view.revision ? ` (revision ${view.revision.slice(0, 8)})` : ""}
              {view.thresholds ? `. Weak signal from ${view.thresholds.weak}, stronger signal from ${view.thresholds.stronger} (average detector output).` : ""}</p>
            <p className="rv-muted">{AI_USE_CAVEAT}</p>
            {field.action && <p className="ai-ack"><Icon name="check" size={15} />{field.action.label}{field.action.note ? `: ${field.action.note}` : ""}</p>}
            <ErrorNotice error={error} />
            {field.result && !locked && (
              <div className="ai-actions">
                <Button variant="secondary" disabled={busy} onClick={() => void act("no_action")}>No action needed</Button>
                {writing ? (
                  <div className="ai-note">
                    <label className="field"><span>Private note (only officers see it)</span><textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} /></label>
                    <Button variant="secondary" disabled={busy || !note.trim()} onClick={() => void act("note")}>Save note</Button>
                  </div>
                ) : <Button variant="secondary" disabled={busy} onClick={() => setWriting(true)}>Add a private note</Button>}
                <Button variant="secondary" disabled={busy} onClick={() => void act("ask")}>Ask the applicant to explain in their own words</Button>
              </div>
            )}
          </>
        ) : (
          <>
            <p className="rv-muted">A draft. Nothing is sent from the app until you approve it. It does not mention any check.</p>
            <label className="field"><span>Message</span><textarea rows={10} value={message} onChange={(e) => setMessage(e.target.value)} /></label>
            <ErrorNotice error={error} />
            <div className="dialog-actions">
              <Button variant="secondary" onClick={onClose}>Keep as draft</Button>
              <Button disabled={busy || !message.trim()} onClick={() => void send()}>Approve and mark as waiting for applicant</Button>
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
