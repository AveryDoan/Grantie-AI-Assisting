// The officer's own mark (0 to 100) for a merit criterion, with a reason. The AI never suggests, pre-fills or estimates it:
// it starts empty ("Not marked"). A mark cannot be saved without a reason. A criterion can instead be set to "Not assessed".
// There is no total, average or comparison anywhere. In the table the three parts sit in their own columns; the viewer stacks them.
import { useState } from "react";
import { api, type Finding } from "../../api";
import { Button, ErrorNotice } from "../../ui";
import { canSave, validMark } from "./marks";

export function useMark(f: Finding, applicationId: string, onChanged: () => void) {
  const saved = f.merit_mark ?? null;
  const [markText, setMarkText] = useState(saved && saved.mark !== null ? String(saved.mark) : "");
  const [reason, setReason] = useState(saved?.reason ?? "");
  const [notAssessed, setNotAssessed] = useState(Boolean(saved?.not_assessed));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const dirty = saved
    ? (notAssessed !== saved.not_assessed) || (!notAssessed && (markText !== String(saved.mark) || reason.trim() !== (saved.reason ?? "")))
    : notAssessed || markText !== "" || reason.trim() !== "";
  const save = async () => {
    setBusy(true); setError(null);
    try {
      await api.setMeritMark(applicationId, f.rule_code, notAssessed
        ? { mark: null, not_assessed: true, reason: reason.trim() || undefined }
        : { mark: validMark(markText)!, not_assessed: false, reason: reason.trim() });
      onChanged();
    } catch (e) { setError(e); }
    setBusy(false);
  };
  return {
    markText, setMarkText, reason, setReason, notAssessed, setNotAssessed, busy, error, saved, dirty, save,
    ok: canSave(markText, reason, notAssessed), slider: validMark(markText),
    state: saved ? (saved.not_assessed ? "Not assessed" : `Marked ${saved.mark} out of 100`) : "Not marked",
  };
}
type Mark = ReturnType<typeof useMark>;
const stop = { onClick: (e: React.MouseEvent) => e.stopPropagation(), onKeyDown: (e: React.KeyboardEvent) => e.stopPropagation() };

export function MarkInputs({ m, f, locked, labels }: { m: Mark; f: Finding; locked: boolean; labels?: boolean }) {
  const id = `mark-${f.rule_code}`;
  return (
    <div className="rv-mark-score" {...stop}>
      {labels && <label htmlFor={id}>Mark (0-100)</label>}
      <div className="rv-mark-inputs">
        <input id={id} type="number" inputMode="numeric" min={0} max={100} step={1} value={m.markText} disabled={locked || m.notAssessed}
          placeholder="–" aria-label={`Mark for ${f.rule_code}, 0 to 100`} onChange={(e) => m.setMarkText(e.target.value)} aria-describedby={`${id}-state`} />
      </div>
      <small id={`${id}-state`} className={m.saved ? "rv-mark-state set" : "rv-mark-state"}>{m.state}</small>
      <label className="rv-na"><input type="checkbox" checked={m.notAssessed} disabled={locked}
        onChange={(e) => { m.setNotAssessed(e.target.checked); if (e.target.checked) m.setMarkText(""); }} />Not assessed</label>
    </div>
  );
}

export function MarkReason({ m, f, locked, labels, rows = 3 }: { m: Mark; f: Finding; locked: boolean; labels?: boolean; rows?: number }) {
  const id = `mark-${f.rule_code}-why`;
  return (
    <div className="rv-mark-reason" {...stop}>
      {labels && <label htmlFor={id}>Reason for mark {!m.notAssessed && <b aria-hidden="true">*</b>}</label>}
      <textarea id={id} rows={rows} value={m.reason} disabled={locked} aria-label={`Reason for the mark for ${f.rule_code}`} onChange={(e) => m.setReason(e.target.value)}
        placeholder={m.notAssessed ? "Optional" : "What you read and why you gave this mark"} />
      {!m.notAssessed && m.markText !== "" && m.reason.trim() === "" && <small className="rv-mark-need">A mark needs a reason.</small>}
    </div>
  );
}

export function MarkSave({ m, locked }: { m: Mark; locked: boolean }) {
  return (
    <div className="rv-mark-save" {...stop}>
      <Button disabled={locked || m.busy || !m.ok || !m.dirty} onClick={() => void m.save()}>{m.saved ? "Update" : "Save"}</Button>
      <ErrorNotice error={m.error} />
    </div>
  );
}

/** All three parts stacked, for the viewer. */
export function MarkControl({ f, applicationId, locked, onChanged }: { f: Finding; applicationId: string; locked: boolean; onChanged: () => void }) {
  const m = useMark(f, applicationId, onChanged);
  return (
    <div className="rv-mark stacked">
      <MarkInputs m={m} f={f} locked={locked} labels />
      <MarkReason m={m} f={f} locked={locked} labels rows={4} />
      <MarkSave m={m} locked={locked} />
    </div>
  );
}
