// Step 1: Documents. The documents overview (needs-attention first, a plain reason for each), the officer's decision for each
// slot, and "Ask the applicant for more": the officer picks what is missing or unclear, the system DRAFTS a plain-English request,
// the officer edits it and approves it. Nothing is sent until they approve and send it.
import { useEffect, useMemo, useState } from "react";
import { api, type DocRequest, type DocSlot, type DocumentsStep, type StepInfo } from "../../api";
import { Button, ErrorNotice, Icon, Loading, formatDate, useLoad } from "../../ui";
import { NextStep } from "./parts";

const DECISION_LABEL: Record<string, string> = { confirmed: "Confirmed", wrong_slot: "Marked as wrong slot", request_again: "To request again", not_needed: "Not needed" };
const KIND_WORDS: Record<string, string> = { missing: "Not uploaded", wrong_slot: "Wrong kind of document", unreadable: "Could not be read", differs: "Does not match the form", other: "Needs another look" };

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

function SlotRow({ slot, busy, onDecide }: { slot: DocSlot; busy: boolean; onDecide: (decision: string, reason?: string) => void }) {
  const [why, setWhy] = useState<string | null>(null);
  const need = slot.issue ? "attention" : slot.document_id ? "ok" : slot.required ? "attention" : "ok";
  return (
    <>
      <tr className={`rv-row need-${need}`}>
        <td><strong>{slot.label}</strong>{!slot.required && <small className="rv-sub">Optional</small>}
          {slot.issue && <small className="rv-reasonline">{slot.issue.reason}</small>}</td>
        <td className="col-low rv-file">{slot.file_name ?? "–"}</td>
        <td><span className={`rv-need lvl-${need}`}><Icon name={need === "ok" ? "check" : "info"} size={14} />{slot.issue ? KIND_WORDS[slot.issue.kind] : need === "ok" ? "Looks right" : "Not uploaded"}</span></td>
        <td>{slot.decision
          ? <span className={`rv-decision ${slot.decision === "confirmed" ? "done" : ""}`}><Icon name={slot.decision === "confirmed" ? "check" : "clock"} size={14} />{DECISION_LABEL[slot.decision]}{slot.reason ? `: ${slot.reason}` : ""}</span>
          : <span className="rv-decision"><Icon name="clock" size={14} />Not decided</span>}</td>
        <td>
          <div className="gf-actions">
            <Button variant={slot.decision === "confirmed" ? "secondary" : "primary"} icon="check" disabled={busy || !slot.document_id || slot.decision === "confirmed"} onClick={() => onDecide("confirmed")}>Confirm</Button>
            <Button variant="secondary" disabled={busy || !slot.document_id || slot.decision === "wrong_slot"} onClick={() => onDecide("wrong_slot")}>Mark as wrong slot</Button>
            <Button variant="secondary" disabled={busy || slot.decision === "request_again"} onClick={() => onDecide("request_again")}>Request again</Button>
            <Button variant="quiet" disabled={busy} onClick={() => setWhy(why === null ? "" : null)}>Not needed</Button>
          </div>
        </td>
      </tr>
      {why !== null && (
        <tr><td colSpan={5} className="gf-inline">
          <label className="field"><span>Why is this not needed? <b>*</b></span>
            <input value={why} onChange={(e) => setWhy(e.target.value)} placeholder="For example: the applicant has no photo to give" /></label>
          <Button disabled={busy || !why.trim()} onClick={() => { onDecide("not_needed", why.trim()); setWhy(null); }}>Record</Button>
        </td></tr>
      )}
      {slot.replaces && <tr><td colSpan={5}><Compare slot={slot} /></td></tr>}
    </>
  );
}

function RequestPanel({ id, data, onChanged }: { id: string; data: DocumentsStep; onChanged: () => void }) {
  const open = data.requests.find((r) => r.status === "draft") ?? null;
  const sent = data.requests.filter((r) => r.status === "sent");
  const [picked, setPicked] = useState<Record<string, { on: boolean; note: string }>>({});
  const [text, setText] = useState(open?.message_text ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { setText(open?.message_text ?? ""); }, [open?.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  const suggested = useMemo(() => new Map(data.suggested_items.map((s) => [s.slot, s])), [data]);
  const act = async (fn: () => Promise<unknown>) => { setBusy(true); setError(null); try { await fn(); onChanged(); } catch (e) { setError(e); } setBusy(false); };

  const chosen = data.slots.filter((s) => picked[s.slot]?.on ?? suggested.has(s.slot));
  const draft = () => act(() => api.draftRequest(id, chosen.map((s) => ({ slot: s.slot, kind: suggested.get(s.slot)?.kind ?? "other", note: picked[s.slot]?.note || undefined }))));

  return (
    <section className="panel gf-request">
      <div className="panel-heading"><div><p className="eyebrow">Ask the applicant for more</p><h2>Request more documents</h2>
        <p>Choose what is missing or unclear. We draft a plain-English request. You edit it and approve it. <strong>Nothing is sent until you approve and send it.</strong></p></div></div>
      <ErrorNotice error={error} />
      {!open && (
        <>
          <ul className="gf-pick">
            {data.slots.map((s) => {
              const sug = suggested.get(s.slot);
              const on = picked[s.slot]?.on ?? Boolean(sug);
              return (
                <li key={s.slot}>
                  <label><input type="checkbox" checked={on} onChange={(e) => setPicked({ ...picked, [s.slot]: { on: e.target.checked, note: picked[s.slot]?.note ?? "" } })} />
                    <span><strong>{s.label}</strong>{sug && <small>{sug.reason}</small>}</span></label>
                  {on && <input className="gf-note" aria-label={`Extra words for ${s.label}`} placeholder="Add your own words (optional)" value={picked[s.slot]?.note ?? ""}
                    onChange={(e) => setPicked({ ...picked, [s.slot]: { on: true, note: e.target.value } })} />}
                </li>
              );
            })}
          </ul>
          <Button icon="file" disabled={busy || chosen.length === 0} onClick={() => void draft()}>Draft the request</Button>
        </>
      )}
      {open && (
        <div className="gf-draft">
          <p className="notice"><Icon name="info" />This is a draft. The applicant has not seen it. Edit it, then approve and send it.</p>
          <label className="field"><span>Request to the applicant</span><textarea rows={14} value={text} onChange={(e) => setText(e.target.value)} /></label>
          <div className="rule-actions">
            <Button variant="secondary" icon="edit" disabled={busy || text === open.message_text || !text.trim()} onClick={() => void act(() => api.editRequest(open.id, text))}>Save my edits</Button>
            <Button icon="send" disabled={busy || text !== open.message_text} title={text !== open.message_text ? "Save your edits first" : undefined}
              onClick={() => void act(() => api.sendRequest(open.id))}>Approve and send</Button>
          </div>
          <small className="muted">This prototype records the request as sent. No email is delivered.</small>
        </div>
      )}
      {sent.length > 0 && (
        <div className="gf-history"><h3>Requests sent</h3>
          {sent.map((r: DocRequest) => (
            <details key={r.id}>
              <summary>Sent {formatDate(r.sent_at, true)} · {r.items.map((i) => i.label).join(", ")}
                {r.resubmitted_at ? <span className="gf-chip resub"><Icon name="check" size={13} />Resubmitted {formatDate(r.resubmitted_at)}</span> : <span className="gf-chip wait"><Icon name="clock" size={13} />Waiting for the applicant</span>}</summary>
              <pre className="doc-text">{r.message_text}</pre>
            </details>
          ))}
        </div>
      )}
    </section>
  );
}

export default function Step1Documents({ id, info, onChanged, onAdvance }: { id: string; info: StepInfo; onChanged: () => void; onAdvance: () => void }) {
  const { data, error, reload } = useLoad(() => api.documentsStep(id), [id]);
  const { data: health } = useLoad(api.health, []);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
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
        <div className="rv-table-wrap" role="region" aria-label="Documents" tabIndex={-1}>
          <table className="rv-table">
            <thead><tr><th>Document</th><th className="col-low">File name</th><th>Check</th><th>Your decision</th><th>Actions</th></tr></thead>
            <tbody>{slots.map((s) => <SlotRow key={s.slot} slot={s} busy={busy || waiting}
              onDecide={(decision, reason) => void act(() => api.documentDecision(id, { slot: s.slot, decision, reason }))} />)}</tbody>
          </table>
        </div>
      </section>
      <RequestPanel id={id} data={data} onChanged={both} />
      <NextStep label="Next step: Redaction check" missing={waiting ? ["Waiting for the applicant to reply to your request", ...data.missing] : data.missing} busy={busy}
        note="Every required document is confirmed or has a reason. Next, the personal details are hidden and you check them." onClick={() => void (async () => { setBusy(true); setFailure(null); try { await api.completeDocuments(id); onChanged(); onAdvance(); } catch (e) { setFailure(e); } setBusy(false); })()} />
    </>
  );
}
