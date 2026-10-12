// Step 1: Documents. The overview keeps the list compact; detailed checks and decisions live in the document review panel.
// "Ask the applicant for more" drafts a plain-English request which the officer edits and approves before sending.
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api, type DocSlot, type StepInfo } from "../../api";
import Composer from "../Composer";
import { Button, ErrorNotice, Icon, Loading, formatDate, useLoad } from "../../ui";
import { NextStep } from "./parts";

import { PdfViewer } from "../review/PdfViewer";

const DECISION_LABEL: Record<string, string> = { confirmed: "Confirmed", wrong_slot: "Marked as wrong document", request_again: "Replacement requested", not_needed: "Not required" };

function Compare({ slot }: { slot: DocSlot }) {
  if (!slot.replaces) return null;
  const { old, new: next } = slot.replaces;
  const fieldKeys = [...new Set([...Object.keys(old.fields), ...Object.keys(next.fields)])].filter((k) => !k.endsWith("_iso") && old.fields[k] !== next.fields[k]);
  return (
    <div className="gf-compare">
      <h4><span className="gf-chip resub"><Icon name="check" size={13} />Resubmitted</span> Compare the new file with the earlier one</h4>
      <div className="gf-sides">
        <div><strong>Earlier file</strong><small>{old.file_name}</small><pre className="doc-text">{(old.text ?? "No text could be read.").slice(0, 700)}</pre></div>
        <div><strong>New file</strong><small>{next.file_name}</small><pre className="doc-text">{(next.text ?? "No text could be read.").slice(0, 700)}</pre></div>
      </div>
      {fieldKeys.length > 0 && (
        <table className="data-table"><thead><tr><th>What changed</th><th>Earlier</th><th>New</th></tr></thead>
          <tbody>{fieldKeys.map((k) => <tr key={k}><td>{k.replace(/_/g, " ")}</td><td>{old.fields[k] ?? "–"}</td><td>{next.fields[k] ?? "–"}</td></tr>)}</tbody></table>
      )}
    </div>
  );
}

function TextPreview({ docId }: { docId: string }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { api.documentText(docId).then((d) => setText(d.text), setError); }, [docId]);
  if (error) return <ErrorNotice error={error} />;
  if (text === null) return <Loading />;
  return text.trim() ? <pre className="dr-text" tabIndex={0} aria-label="Document text">{text}</pre>
    : <div className="dr-empty"><Icon name="file" size={28} /><p>This file has no text to show.</p></div>;
}

function reviewStatus(slot: DocSlot) {
  if (slot.decision) return slot.decision === "confirmed" ? { tone: "ok", label: "Reviewed" } : { tone: "attention", label: DECISION_LABEL[slot.decision] };
  if (slot.issue || (slot.required && !slot.document_id)) return { tone: "attention", label: "Needs attention" };
  if (slot.document_id) return { tone: "neutral", label: "Not reviewed" };
  return { tone: "neutral", label: "Optional" };
}

function DocumentReviewPanel({ appId, slot, busy, onClose, onDecide, onRemind }: {
  appId: string; slot: DocSlot; busy: boolean; onClose: () => void; onDecide: (decision: string, reason?: string) => Promise<void>; onRemind: () => void;
}) {
  const [reason, setReason] = useState(slot.reason ?? "");
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const status = reviewStatus(slot);
  const hasPdf = Boolean(slot.document_id && slot.file_name?.toLowerCase().endsWith(".pdf"));
  const decide = async (decision: string) => {
    if ((decision === "request_again" || decision === "not_needed") && !reason.trim()) {
      setError(new Error(decision === "not_needed" ? "Enter why this document is not required." : "Enter a reason before requesting a replacement document."));
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onDecide(decision, reason.trim() || undefined);
      onClose();
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  };

  return (
    createPortal(
      <div className="dr-wrap" role="dialog" aria-modal="true" aria-label={`Review ${slot.label}`}>
        <div className="dr-backdrop" onClick={onClose} aria-hidden="true" />
        <section className="dr-panel">
          <header className="dr-header">
            <div><p className="eyebrow">Document review</p><h2>{slot.label}</h2><p className="muted">{slot.required ? "Required document" : "Optional document"}</p></div>
            <Button variant="secondary" icon="close" onClick={onClose}>Close</Button>
          </header>
          <div className="dr-content">
            <div className="dr-viewer">
              <div className="dr-file-heading"><strong>{slot.file_name ?? "No file uploaded"}</strong>{hasPdf && <small>PDF document</small>}</div>
              {hasPdf ? <PdfViewer docId={slot.document_id!} appId={appId} blur={false} height="min(66vh, 720px)" /> : slot.document_id ? <TextPreview docId={slot.document_id} /> : <div className="dr-empty"><Icon name="file" size={28} /><p>{slot.document_id ? "This document is not a PDF and cannot be previewed here." : "No document has been uploaded for this slot."}</p></div>}
            </div>
            <aside className="dr-info">
              <div className="dr-status"><span className={`dr-badge ${status.tone}`}><Icon name={status.tone === "ok" ? "check" : status.tone === "attention" ? "info" : "clock"} size={14} />{status.label}</span></div>
              <section><h3>Document information</h3><dl><dt>Type</dt><dd>{slot.label}</dd><dt>File</dt><dd className="dr-filename" title={slot.file_name ?? undefined}>{slot.file_name ?? "Not uploaded"}</dd><dt>Requirement</dt><dd>{slot.required ? "Mandatory" : "Optional"}</dd></dl></section>
              <section><h3>Automated check</h3><p className={slot.issue ? "dr-warning" : "dr-success"}><Icon name={slot.issue ? "info" : "check"} size={15} />{slot.issue ? slot.issue.reason : slot.document_id ? "The uploaded file looks like the expected document type." : "No automated check is available yet."}</p></section>
              {slot.replaces && <details><summary>Compare with earlier upload</summary><Compare slot={slot} /></details>}
              <label className="field dr-reason"><span>Decision note <small>(required for "Request new document" and "Not required")</small></span><textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Add a short explanation for the record" /></label>
              <ErrorNotice error={error} />
              <div className="dr-actions">
                <Button icon="check" disabled={saving || busy || !slot.document_id} onClick={() => void decide("confirmed")}>Confirm document</Button>
                <Button variant="secondary" disabled={saving || busy || !slot.document_id} onClick={() => void decide("wrong_slot")}>Mark as wrong document</Button>
                <Button variant="secondary" disabled={saving || busy} onClick={() => void decide("request_again")}>Request new document</Button>
                <Button variant="quiet" disabled={saving || busy} onClick={() => void decide("not_needed")}>Not required</Button>
              </div>
              {slot.requested && (
                <p className="dr-requested" role="status"><Icon name="clock" size={14} />Requested on {formatDate(slot.requested.sent_at)}{slot.requested.due_date ? `, due ${formatDate(slot.requested.due_date)}` : ""}.{" "}
                  <details><summary>View email</summary><pre className="doc-text">{slot.requested.message_text}</pre></details>
                  <Button variant="secondary" icon="send" onClick={onRemind}>Send reminder</Button></p>
              )}
              {slot.decision && <p className="muted dr-existing"><Icon name="check" size={14} />Existing decision: {DECISION_LABEL[slot.decision]}{slot.reason ? ` — ${slot.reason}` : ""}</p>}
            </aside>
          </div>
        </section>
      </div>,
      document.body,
    )
  );
}

export default function Step1Documents({ id, info, onChanged, onAdvance }: { id: string; info: StepInfo; onChanged: () => void; onAdvance: () => void }) {
  const { data, error, reload } = useLoad(() => api.documentsStep(id), [id]);
  const { data: health } = useLoad(api.health, []);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [openSlot, setOpenSlot] = useState<DocSlot | null>(null);
  const [compose, setCompose] = useState<{ slots: string[]; note: string } | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  if (error) return <ErrorNotice error={error} />;
  if (!data) return <Loading />;

  const rank = (s: DocSlot) => (s.issue || (s.required && !s.document_id) ? 0 : s.decision ? 2 : 1);
  const slots = [...data.slots].sort((a, b) => rank(a) - rank(b));
  const needs = data.slots.filter((s) => s.issue || (s.required && !s.document_id)).length;
  const both = () => { reload(); onChanged(); };
  const act = async (fn: () => Promise<unknown>) => { setBusy(true); setFailure(null); try { await fn(); both(); } catch (e) { setFailure(e); } setBusy(false); };
  const waiting = info.status === "waiting";

  return (
    <>
      <section className="panel">
        <div className="panel-heading"><div><p className="eyebrow">Step 1 of 4</p><h2>Documents</h2>
          <p>Check each document is the right one and in the right place. Documents that need a look are at the top, with the reason.</p></div></div>
        {waiting && (
          <div className="notice blue gf-notice"><Icon name="user" /><span><strong>Waiting for the applicant.</strong> A request was sent. This step carries on when they reply.
            {health?.mode === "demo" && <> <Button variant="secondary" disabled={busy} onClick={() => void act(() => api.demoApplicantReply(id))}>Demo: send the applicant’s reply</Button></>}</span></div>
        )}
        <p className={`rv-counter ${needs ? "on" : ""}`} role="status"><Icon name={needs ? "info" : "check"} size={16} />{needs === 0 ? "No documents need attention" : `${needs} ${needs === 1 ? "document needs" : "documents need"} attention`}</p>
        <ErrorNotice error={failure} />
        {toast && <p className="notice ok-notice" role="status"><Icon name="check" />{toast} <button className="link-button" onClick={() => setToast(null)}>Dismiss</button></p>}
        <div className="rv-toolrow"><Button variant="secondary" icon="send" disabled={busy || waiting} onClick={() => setCompose({ slots: [], note: "" })}>Ask applicant for more</Button></div>
        <div className="rv-table-wrap" role="region" aria-label="Documents" tabIndex={-1}>
          <table className="rv-table rv-documents-clean">
            <thead><tr><th>Document type</th><th>File name</th><th>Review status</th><th aria-label="Open document" /></tr></thead>
            <tbody>{slots.map((s) => {
              const status = reviewStatus(s);
              return <tr key={s.slot} className={`rv-row need-${status.tone}`} onClick={() => setOpenSlot(s)}>
                <td><strong>{s.label}</strong>{!s.required && <small className="rv-sub">Optional</small>}</td>
                <td className="rv-file" title={s.file_name ?? undefined}>{s.file_name ?? "Not uploaded"}</td>
                <td><span className={`dr-badge ${status.tone}`}><Icon name={status.tone === "ok" ? "check" : status.tone === "attention" ? "info" : "clock"} size={14} />{status.label}</span></td>
                <td><Button variant="quiet" icon="file" disabled={busy || waiting} onClick={() => setOpenSlot(s)}>{s.document_id ? "Open document" : "Review"}</Button></td>
              </tr>;
            })}</tbody>
          </table>
        </div>
      </section>
      <NextStep label="Next step: Redaction check" missing={waiting ? ["Waiting for the applicant to reply to your request", ...data.missing] : data.missing} busy={busy}
        note="Every required document is confirmed or has a reason. Next, the personal details are hidden and you check them." onClick={() => void (async () => { setBusy(true); setFailure(null); try { await api.completeDocuments(id); onChanged(); onAdvance(); } catch (e) { setFailure(e); } setBusy(false); })()} />
      {openSlot && !compose && <DocumentReviewPanel appId={id} slot={openSlot} busy={busy || waiting} onClose={() => setOpenSlot(null)}
        onRemind={() => setCompose({ slots: [openSlot.slot], note: "" })}
        onDecide={async (decision, reason) => {
          await act(() => api.documentDecision(id, { slot: openSlot.slot, decision, reason }));
          // a document that needs replacing is asked for in the email composer, opened straight away
          if (decision === "request_again" || decision === "wrong_slot") setCompose({ slots: [openSlot.slot], note: reason ?? "" });
        }} />}
      {compose && <Composer appId={id} slots={compose.slots} note={compose.note} onClose={() => { setCompose(null); setOpenSlot(null); }}
        onDone={(m) => { setCompose(null); setOpenSlot(null); setToast(m); both(); }} />}
    </>
  );
}
