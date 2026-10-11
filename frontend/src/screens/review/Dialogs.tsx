// Dialogs for deciding a rule: override (reason required), confirm "Not met" (typed reason), and asking the applicant.
import { useEffect, useState } from "react";
import { api, type Decision, type Detail, type EvidenceDraft, type Finding } from "../../api";
import { Button, ErrorNotice, Icon, StatusChip } from "../../ui";

export type DialogState =
  | { kind: "override"; finding: Finding }
  | { kind: "confirm-not-met"; finding: Finding }
  | { kind: "ask"; finding: Finding; more?: Finding[] };

export function DecisionDialog({ state, onDone, close }: { state: Exclude<DialogState, { kind: "ask" }>; onDone: () => void; close: () => void }) {
  const f = state.finding;
  const fixed = state.kind === "confirm-not-met";
  const [status, setStatus] = useState<Decision | "">(fixed ? "Not met" : "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const title = { override: "Override AI suggestion", "confirm-not-met": "Confirm “Not met”" }[state.kind];
  const intro = {
    override: "Choose your finding and explain why. Your reason will be saved in the audit trail.",
    "confirm-not-met": "A finding of “Not met” always needs a typed reason. It will appear in the applicant’s letter.",
  }[state.kind];

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.review(f.id, { action: fixed ? "confirm" : "override", final_status: status as Decision, reason: reason.trim() });
      onDone();
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="decision-title">
        <div className="dialog-head">
          <div><p className="eyebrow">Rule {f.rule_code}</p><h2 id="decision-title">{title}</h2></div>
          <button className="icon-button" onClick={close} aria-label="Close dialog"><Icon name="close" /></button>
        </div>
        <p>{intro}</p>
        {!fixed && (
          <fieldset><legend>New status <span aria-hidden="true">*</span></legend>
            <div className="status-options">
              {(["Met", "Not met", "Needs evidence", "Unclear"] as Decision[]).map((item) => (
                <label key={item} className={status === item ? "selected" : ""}>
                  <input type="radio" name="newStatus" value={item} checked={status === item} onChange={() => setStatus(item)} />
                  <StatusChip status={item} />
                </label>
              ))}
            </div>
          </fieldset>
        )}
        <label className="field">
          <span>Reason <b>*</b></span>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Explain what evidence supports your decision" rows={4} />
          <small>{status === "Not met" ? "A clear justification is required for a finding of Not met." : "This helps another officer understand your decision."}</small>
        </label>
        <ErrorNotice error={error} />
        <div className="dialog-actions">
          <Button variant="secondary" onClick={close}>Cancel</Button>
          <Button disabled={busy || !status || !reason.trim()} onClick={() => void save()}>Save decision</Button>
        </div>
      </div>
    </div>
  );
}

export function AskDialog({ detail, findings, onDone, close }: { detail: Detail; findings: Finding[]; onDone: () => void; close: () => void }) {
  const [draft, setDraft] = useState<EvidenceDraft | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [copied, setCopied] = useState(false);

  // Opening this marks the rule Needs evidence (an officer action, audited) and drafts the request from all Needs-evidence rules.
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        for (const f of findings) await api.review(f.id, { action: "ask_applicant" });
        const d = await api.draftEvidenceRequest(detail.application.id, findings.map((f) => f.id));
        if (live) { setDraft(d); setMessage(d.message_text); setBusy(false); }
      } catch (e) { if (live) { setError(e); setBusy(false); } }
    })();
    return () => { live = false; };
  }, [findings.map((f) => f.id).join(), detail.application.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const copy = async () => { try { await navigator.clipboard.writeText(message); setCopied(true); } catch { setCopied(false); } };
  const download = () => {
    const url = URL.createObjectURL(new Blob([`To: ${draft?.to ?? ""}\nSubject: ${draft?.subject ?? ""}\n\n${message}`], { type: "text/plain" }));
    const a = document.createElement("a"); a.href = url; a.download = "evidence-request.txt"; a.click(); URL.revokeObjectURL(url);
  };
  const waiting = async () => {
    if (!draft) return;
    setBusy(true); setError(null);
    try { await api.editRequest(draft.id, message.trim()); await api.sendRequest(draft.id); onDone(); } catch (e) { setError(e); setBusy(false); }
  };

  return (
    <div className="modal-backdrop">
      <div className="dialog wide" role="dialog" aria-modal="true" aria-labelledby="ask-title">
        <div className="dialog-head">
          <div><p className="eyebrow">Ask applicant for more · {findings.map((f) => f.rule_code).join(", ")}</p><h2 id="ask-title">Evidence request (draft)</h2></div>
          <button className="icon-button" onClick={close} aria-label="Close dialog"><Icon name="close" /></button>
        </div>
        {busy && !draft && <p className="muted">Preparing the draft…</p>}
        {draft && (
          <>
            <dl className="kv"><dt>To</dt><dd>{draft.to || "(no email on file)"}</dd><dt>Subject</dt><dd>{draft.subject}</dd></dl>
            <p className="muted">Rules asked about: {draft.items.map((i) => i.rule_code).join(", ")}</p>
            <label className="field"><span>Message</span><textarea rows={12} value={message} onChange={(e) => setMessage(e.target.value)} /></label>
            <div className="notice"><Icon name="info" />{draft.not_sent_note} Copy or download it, send it yourself, then mark the application as waiting for the applicant.</div>
          </>
        )}
        <ErrorNotice error={error} />
        <div className="dialog-actions">
          <Button variant="secondary" onClick={close}>Close</Button>
          <Button variant="secondary" disabled={!draft} onClick={() => void copy()}>{copied ? "Copied" : "Copy"}</Button>
          <Button variant="secondary" disabled={!draft} onClick={download}>Download .txt</Button>
          <Button disabled={busy || !draft || !message.trim()} onClick={() => void waiting()}>Mark as waiting for applicant</Button>
        </div>
      </div>
    </div>
  );
}

