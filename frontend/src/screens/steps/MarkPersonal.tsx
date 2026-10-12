// "Mark as personal": the same dialog from the Add-a-missed-item box and from the side-by-side viewer. A searchable, grouped list of
// what the selection is; the full selected text (never cut off); how many places will change; the placeholder it will become; and
// a warning when a rule needs the value. Redaction and the leak scan run again after saving, and the change is logged.
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, type AddPreview, type RedactionOption } from "../../api";
import { Button, ErrorNotice, Icon } from "../../ui";

export function MarkPersonal({ appId, source, text, onReselect, onClose, onDone }: {
  appId: string; source: string; text: string; onReselect: () => void; onClose: () => void; onDone: () => void;
}) {
  const [options, setOptions] = useState<RedactionOption[]>([]);
  const [find, setFind] = useState("");
  const [kind, setKind] = useState("");
  const [description, setDescription] = useState("");
  const [every, setEvery] = useState(true);
  const [matchCase, setMatchCase] = useState(false);
  const [preview, setPreview] = useState<AddPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const search = useRef<HTMLInputElement>(null);
  useEffect(() => { void api.redactionOptions().then(setOptions).catch(setError); search.current?.focus(); }, []);
  useEffect(() => {
    if (!kind) { setPreview(null); return; }
    let live = true;
    api.addPreview(appId, { source, text, kind, match_case: matchCase, every }).then((p) => live && setPreview(p)).catch((e) => live && setError(e));
    return () => { live = false; };
  }, [kind, matchCase, every, text, source, appId]);
  const groups = useMemo(() => {
    const q = find.trim().toLowerCase();
    const m = new Map<string, RedactionOption[]>();
    for (const o of options.filter((x) => !q || x.label.toLowerCase().includes(q) || x.group.toLowerCase().includes(q))) m.set(o.group, [...(m.get(o.group) ?? []), o]);
    return [...m];
  }, [options, find]);
  const chosen = options.find((o) => o.key === kind);
  const needsDescription = kind === "other" && !description.trim();
  const save = async () => {
    setBusy(true); setError(null);
    try { await api.addMissed(appId, { source, text, kind, description: description || undefined, match_case: matchCase, every }); onDone(); }
    catch (e) { setError(e); setBusy(false); }
  };
  return createPortal(
    <div className="modal-backdrop">
      <div className="db-dialog mp" role="dialog" aria-modal="true" aria-labelledby="mp-title" onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}>
        <div className="db-dialog-head"><h2 id="mp-title">Mark as personal</h2><button className="icon-button" onClick={onClose} aria-label="Close"><Icon name="close" /></button></div>
        <p className="mp-selected"><b>Selected text</b><span>{text}</span> <button className="link-button" onClick={onReselect}>Reselect</button></p>
        <label className="field"><span>What is it? (type to search)</span>
          <input ref={search} value={find} onChange={(e) => setFind(e.target.value)} placeholder="For example: passport, employer, health" /></label>
        <div className="mp-list" role="listbox" aria-label="What it is">
          {groups.map(([g, opts]) => (
            <div key={g} role="group" aria-label={g}><h3>{g}</h3>
              {opts.map((o) => (
                <button key={o.key} role="option" aria-selected={kind === o.key} className={kind === o.key ? "on" : ""} onClick={() => setKind(o.key)}>{o.label}</button>
              ))}</div>
          ))}
          {groups.length === 0 && <p className="muted">Nothing matches. Try another word.</p>}
        </div>
        {kind === "other" && <label className="field"><span>Describe it in one line <b>*</b></span><input value={description} maxLength={200} onChange={(e) => setDescription(e.target.value)} /></label>}
        <div className="mp-opts">
          <label><input type="checkbox" checked={every} onChange={(e) => setEvery(e.target.checked)} /> Redact every matching occurrence in this application</label>
          <label><input type="checkbox" checked={matchCase} onChange={(e) => setMatchCase(e.target.checked)} /> Match case</label>
        </div>
        {preview && chosen && (
          <p className="mp-preview" role="status"><Icon name="eye" size={16} /> {preview.matches} {preview.matches === 1 ? "place" : "places"} will change. It will read <code>{preview.token_preview}</code>.</p>
        )}
        {preview?.warning && <p className="notice warn-notice" role="alert"><Icon name="info" /><span><b>{preview.warning}</b> ({preview.protected.join(", ")})</span></p>}
        <ErrorNotice error={error} />
        <div className="rd-dialog-actions">
          <Button disabled={busy || !kind || needsDescription} onClick={() => void save()}>Mark as personal</Button>
          <Button variant="quiet" onClick={onClose}>Cancel</Button>
        </div>
        <small className="muted">Redaction and the leak scan run again after you save. This is logged as added by an officer.</small>
      </div>
    </div>,
    document.body,
  );
}
