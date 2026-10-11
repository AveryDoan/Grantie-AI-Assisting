// Step 2: Redaction check. The AI cannot read this application until the officer approves this step.
// The table groups what was redacted by type. A click shows every item with the redacted text around it; the original value stays
// hidden unless the officer asks to reveal it, and every reveal is recorded in the audit log. The officer can add a missed item,
// unmask something that is not personal (with a reason) and approve. A failed leak scan blocks approval.
import { Fragment, useRef, useState } from "react";
import { api, type RedactionCheck, type RedactionGroup, type RedactionItem, type StepInfo } from "../../api";
import { Button, ErrorNotice, Icon, Loading, formatDate, useLoad } from "../../ui";
import { NextStep } from "./parts";
import { PdfModal } from "../review/PdfModal";

const ADD_TYPES = ["Name", "Email", "Phone", "Address", "ID number", "Date of birth"];

function Items({ id, type, onChanged }: { id: string; type: string; onChanged: () => void }) {
  const { data, error, reload } = useLoad(() => api.redactionItems(id, type), [id, type]);
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [unmask, setUnmask] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  if (error) return <ErrorNotice error={error} />;
  if (!data) return <Loading />;
  const reveal = async (it: RedactionItem) => {
    setBusy(true); setFailure(null);
    try { setRevealed({ ...revealed, [it.id]: (await api.revealItem(id, it.id)).original }); } catch (e) { setFailure(e); }
    setBusy(false);
  };
  const doUnmask = async (it: RedactionItem) => {
    setBusy(true); setFailure(null);
    try { await api.unmaskItem(id, it.id, reason); setUnmask(null); setReason(""); reload(); onChanged(); } catch (e) { setFailure(e); }
    setBusy(false);
  };
  return (
    <div className="gf-items">
      <ErrorNotice error={failure} />
      <p className="muted">The original is hidden. Revealing a value is recorded in the audit log (who, when and which item, never the value).</p>
      <ul>
        {data.map((it) => (
          <li key={it.id}>
            <p className="gf-context"><span className="muted">{it.before}</span><mark className="token-mark">{it.token}</mark><span className="muted">{it.after}</span></p>
            <small className="muted">{it.where}</small>
            <div className="gf-actions">
              {revealed[it.id] === undefined
                ? <Button variant="secondary" icon="user" disabled={busy} onClick={() => void reveal(it)}>Reveal original</Button>
                : <><span className="gf-original" aria-live="polite">Original: <strong>{revealed[it.id]}</strong></span>
                    <Button variant="quiet" onClick={() => { const { [it.id]: _x, ...rest } = revealed; setRevealed(rest); }}>Hide</Button></>}
              <Button variant="quiet" disabled={busy} onClick={() => setUnmask(unmask === it.id ? null : it.id)}>This is not personal</Button>
            </div>
            {unmask === it.id && (
              <div className="gf-inline">
                <label className="field"><span>Why is this not personal? <b>*</b></span><input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: the name of a public place" /></label>
                <Button disabled={busy || !reason.trim()} onClick={() => void doUnmask(it)}>Leave it unmasked</Button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function AddMissed({ id, data, onChanged }: { id: string; data: RedactionCheck; onChanged: () => void }) {
  const keys = Object.keys(data.texts);
  const [source, setSource] = useState(keys[0] ?? "application_text");
  const [selected, setSelected] = useState("");
  const [kind, setKind] = useState("Name");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const box = useRef<HTMLPreElement>(null);
  const capture = () => {
    const sel = window.getSelection();
    const text = sel && box.current && sel.anchorNode && box.current.contains(sel.anchorNode) ? sel.toString().trim() : "";
    setSelected(text);
  };
  const add = async () => {
    setBusy(true); setError(null);
    try { await api.addMissed(id, { source, text: selected, kind }); setSelected(""); onChanged(); } catch (e) { setError(e); }
    setBusy(false);
  };
  return (
    <section className="panel">
      <div className="panel-heading"><div><p className="eyebrow">Missed something?</p><h2>Add a missed item</h2>
        <p>Select text in the redacted version below that should have been hidden, choose what it is, and mark it. Redaction then runs again.</p></div></div>
      <ErrorNotice error={error} />
      <div className="rv-tabs" role="tablist" aria-label="Texts">
        {keys.map((k) => <button key={k} role="tab" aria-selected={k === source} className={k === source ? "active" : ""} onClick={() => { setSource(k); setSelected(""); }}>{data.texts[k].label}</button>)}
      </div>
      <pre className="doc-text gf-redtext" ref={box} onMouseUp={capture} onKeyUp={capture} tabIndex={0} aria-label="Redacted text. Select words to mark them.">{data.texts[source]?.redacted}</pre>
      <div className="gf-add">
        <span>{selected ? <>Selected: <strong>“{selected.slice(0, 80)}”</strong></> : "Nothing selected"}</span>
        <label className="sr-only" htmlFor="gf-kind">What is it?</label>
        <select id="gf-kind" value={kind} onChange={(e) => setKind(e.target.value)}>{ADD_TYPES.map((t) => <option key={t}>{t}</option>)}</select>
        <Button variant="secondary" disabled={busy || selected.length < 2} onClick={() => void add()}>Mark as personal</Button>
      </div>
    </section>
  );
}

export default function Step2Redaction({ id, info, onChanged, onAdvance, onBack }: { id: string; info: StepInfo; onChanged: () => void; onAdvance: () => void; onBack: () => void }) {
  const { data, error, reload } = useLoad(() => api.redactionCheck(id), [id, info.status]);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [pdfDoc, setPdfDoc] = useState<RedactionCheck["documents"][number] | null>(null);
  if (error) return <ErrorNotice error={error} />;
  if (!data) return <Loading />;
  const both = () => { reload(); onChanged(); };
  const approve = async () => {
    setBusy(true); setFailure(null);
    try { await api.approveRedaction(id); onChanged(); onAdvance(); } catch (e) { setFailure(e); }
    setBusy(false);
  };
  const done = info.status === "done";
  const groups: RedactionGroup[] = data.groups;

  return (
    <>
      <section className="panel">
        <div className="panel-heading"><div><p className="eyebrow">Step 2 of 4</p><h2>Redaction check</h2>
          <p>Personal details are replaced with placeholders before the AI reads anything. Check what was hidden, then approve. The AI is blocked until you do.</p></div>
          <div className="gf-leak"><span className={`gf-chip ${data.leak_scan.passed ? "ok" : "bad"}`}><Icon name={data.leak_scan.passed ? "check" : "close"} size={14} />Leak scan {data.leak_scan.passed ? "passed" : "failed"}</span>
            {data.run && <small className="muted">Run {formatDate(data.run.finished_at ?? data.run.started_at, true)}</small>}</div></div>
        {!data.leak_scan.passed && (
          <div className="notice error-notice" role="alert"><Icon name="info" /><span><strong>The leak scan found something that looks personal in the redacted text.</strong>{" "}
            Add the missed item below or run redaction again. You cannot approve until the scan passes. ({Object.entries(data.leak_scan.checks).filter(([, n]) => n).map(([k, n]) => `${k.replace(/_/g, " ")}: ${n}`).join(", ")})</span></div>
        )}
        {groups.length === 0 ? <p className="empty-state">Nothing was redacted yet. Redaction runs when step 1 is finished.</p> : (
          <div className="rv-table-wrap" role="region" aria-label="Redacted items by type" tabIndex={-1}>
            <table className="rv-table">
              <thead><tr><th>Type</th><th>Count</th><th>How it was redacted</th><th>Where</th></tr></thead>
              <tbody>
                {groups.map((g) => (
                  <Fragment key={g.type}>
                    <tr key={g.type} className={`rv-row ${open === g.type ? "selected" : ""}`} tabIndex={0} data-row aria-expanded={open === g.type}
                      onClick={() => setOpen(open === g.type ? null : g.type)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(open === g.type ? null : g.type); } }}>
                      <td><strong>{g.type}</strong></td><td>{g.count}</td>
                      <td>{g.how.map((h) => <code key={h} className="rv-ref">{h}</code>)}</td>
                      <td>{g.where.slice(0, 3).join(" · ")}{g.where.length > 3 ? ` · +${g.where.length - 3} more` : ""}</td>
                    </tr>
                    {open === g.type && <tr key={`${g.type}-items`}><td colSpan={4}><Items id={id} type={g.type} onChanged={both} /></td></tr>}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {(data.edits.added > 0 || data.edits.unmasked > 0) && <p className="muted">Your changes: {data.edits.added} added, {data.edits.unmasked} left unmasked.</p>}
      </section>
      <section className="panel">
        <div className="panel-heading"><div><p className="eyebrow">Look at the pages</p><h2>Original documents, with redaction blurred</h2>
          <p>Every redacted span is blurred on the page. Open a PDF to see it; reveal one item at a time (each reveal is recorded). Copies you download are redacted on the server: black boxes are burned into page images.</p></div>
          <Button variant="secondary" icon="download" onClick={() => void api.downloadPdf(`/applications/${id}/evidence-pack.pdf`, "evidence-pack-redacted.pdf").catch(setFailure)}>Download evidence pack (redacted)</Button></div>
        <div className="rv-table-wrap"><table className="rv-table">
          <thead><tr><th>Document</th><th>Redacted spans</th><th>Actions</th></tr></thead>
          <tbody>{data.documents.map((d) => (
            <tr key={d.id}><td><strong>{d.file_name}</strong></td><td>{d.redacted_spans}</td>
              <td><div className="gf-actions">
                {d.is_pdf && <Button variant="secondary" onClick={() => setPdfDoc(d)}>View PDF with blur</Button>}
                <Button variant="quiet" icon="download" disabled={!d.redacted_spans && !d.is_pdf} onClick={() => void api.downloadPdf(`/documents/${d.id}/redacted.pdf`, `redacted-${d.file_name}`).catch(setFailure)}>Download redacted copy</Button>
              </div></td></tr>))}</tbody>
        </table></div>
      </section>
      {pdfDoc && <PdfModal appId={id} title={pdfDoc.file_name} targets={[{ docId: pdfDoc.id, title: pdfDoc.file_name, items: [] }]} blur onClose={() => setPdfDoc(null)} />}
      <AddMissed id={id} data={data} onChanged={both} />
      <ErrorNotice error={failure} />
      <div className="gf-approve">
        <Button variant="secondary" icon="left" onClick={onBack}>Back to documents</Button>
        <div className="gf-approve-main">
          <NextStep label={done ? "Redaction check approved" : "Approve all and let the AI read the redacted version"} busy={busy}
            missing={done ? [] : data.blockers} onClick={() => (done ? onAdvance() : void approve())} />
          <p className="gf-aiwill"><Icon name="shield" size={16} />AI will read the redacted version only.</p>
        </div>
      </div>
    </>
  );
}
