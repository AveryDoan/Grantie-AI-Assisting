// Dialogs for deciding a rule: override (reason required), confirm "Not met" (typed reason), and asking the applicant.
import { useState } from "react";
import { api, type Decision, type Finding } from "../../api";
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

