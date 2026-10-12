// Shared by the Outcomes page: releasing signed-off letters (only ones this officer opened, after typing the count) and the
// type-the-count confirmation used before any batch action.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { api, type HeldLetter } from "../api";
import { Button, ErrorNotice, Icon } from "../ui";

export function BatchRelease({ held, onDone }: { held: HeldLetter[] | null; onDone: () => void }) {
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

export function TypeCount({ title, count, action, busy, onCancel, onConfirm, children }: {
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
