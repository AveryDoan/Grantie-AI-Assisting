// The officer's own decision for a rule that has no AI result (officer judgement outside the merit marks).
import { useState } from "react";
import { api, type Finding } from "../../api";
import { Button, ErrorNotice } from "../../ui";

const CHOICES: { label: string; status: "Met" | "Not met" | "Unclear" }[] = [
  { label: "Meets", status: "Met" }, { label: "Does not meet", status: "Not met" }, { label: "Not assessed", status: "Unclear" },
];

/** The officer's own assessment: a note and a choice. Nothing is pre-filled by AI (only the officer's earlier entry). */
export function AssessForm({ f, locked, onChanged }: { f: Finding; locked: boolean; onChanged: () => void }) {
  const last = f.latest_review;
  const [choice, setChoice] = useState<string>(CHOICES.find((c) => c.status === last?.final_status)?.label ?? "");
  const [note, setNote] = useState(last?.reason ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (locked) return <p className="muted">This application is signed off. Decisions are locked.</p>;
  const save = async () => {
    setBusy(true); setError(null);
    try { await api.review(f.id, { action: "override", final_status: CHOICES.find((c) => c.label === choice)!.status, reason: note.trim() }); onChanged(); } catch (e) { setError(e); }
    setBusy(false);
  };
  return (
    <>
      <label className="field"><span>Your assessment <b>*</b></span>
        <select value={choice} onChange={(e) => setChoice(e.target.value)}>
          <option value="">Choose…</option>
          {CHOICES.map((c) => <option key={c.label}>{c.label}</option>)}
        </select></label>
      <label className="field"><span>Your note <b>*</b></span>
        <textarea rows={5} value={note} onChange={(e) => setNote(e.target.value)} placeholder="What you read and how you weighed it" />
        <small>Saved in the audit trail.</small></label>
      <ErrorNotice error={error} />
      <div className="rule-actions"><Button disabled={busy || !choice || !note.trim()} onClick={() => void save()}>Record assessment</Button></div>
    </>
  );
}

