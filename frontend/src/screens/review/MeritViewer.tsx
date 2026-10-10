// Merit criterion viewer: the whole text on the left (with paragraph and page markers), the AI's notes on the right.
// Every note points at a passage code found in the text. Clicking a note scrolls to and highlights its passage;
// clicking a highlighted passage selects its note. A failed quote or a summary with no source is listed apart, never
// shown as a normal note. There is no score, rating or strength anywhere, and the weight is not shown.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Decision, type Detail, type Finding } from "../../api";
import { Button, ErrorNotice, Icon, StatusChip, effectiveStatus, formatDate } from "../../ui";
import type { DialogState } from "./Dialogs";
import { MAX_QUOTES, MAX_SUMMARIES, highlightsFor, notesOf, sourcesOf, type QuoteNote, type SummaryNote } from "./highlight";
import { CHECKED_BY_LABEL, checkedBy, plainRule } from "./model";
import { TextView } from "./TextView";

const CRITERIA: Record<string, string> = { M1: "Academic Merit", M2: "Supporting Evidence", M3: "Leadership", M4: "Community Engagement", M5: "Short answer" };
const CHOICES: { label: string; status: Decision }[] = [
  { label: "Meets", status: "Met" }, { label: "Does not meet", status: "Not met" }, { label: "Not assessed", status: "Unclear" },
];

function Slot({ n, kind }: { n: number; kind: "quote" | "summary" }) {
  return <div className="rv-slot" aria-label={`Empty ${kind} slot ${n}`}>{kind === "quote" ? "No further passage found" : "No summary for this slot"}</div>;
}

function Card({ note, selected, onSelect, sourceLabel }: { note: QuoteNote | SummaryNote; selected: boolean; onSelect: () => void; sourceLabel: string }) {
  const unlocated = note.state === "unlocated";
  const isQuote = note.kind === "quote";
  return (
    <button className={`rv-note ${note.kind} ${selected ? "sel" : ""}`} aria-pressed={selected} onClick={onSelect} disabled={unlocated}>
      <span className="rv-note-label">{isQuote ? "Verified quote" : "AI summary, check the original"}</span>
      {isQuote && (note as QuoteNote).label && <small className="rv-note-sub">{(note as QuoteNote).label}</small>}
      <span className="rv-note-text">{isQuote ? <>“{note.text}”</> : note.text}</span>
      {unlocated
        ? <small className="rv-note-warn"><Icon name="question" size={14} />Could not locate in the document</small>
        : <small className="rv-note-act"><Icon name="search" size={14} />Show in document · {sourceLabel}</small>}
    </button>
  );
}

export function MeritViewer({ f, detail, locked, busy, onClose, onChanged, onTrace, onDialog, onConfirm }: {
  f: Finding; detail: Detail; locked: boolean; busy: boolean; onClose: () => void; onChanged: () => void; onTrace: (item: string) => void;
  onDialog: (d: DialogState) => void; onConfirm: (f: Finding) => void;
}) {
  const notes = useMemo(() => notesOf(f), [f]);
  const texts = detail.source_texts ?? {};
  const tabs = useMemo(() => sourcesOf(notes).filter((k) => texts[k]), [notes, texts]);
  const fallback = Object.keys(texts)[0];
  const [source, setSource] = useState(tabs[0] ?? fallback ?? "application_text");
  const [selected, setSelected] = useState<string | null>(null);
  const head = useRef<HTMLDivElement>(null);
  useEffect(() => { head.current?.focus(); }, [f.id]);

  const label = (k: string) => texts[k]?.label ?? "Application form";
  const sourceOf = (n: QuoteNote | SummaryNote) => (n.kind === "quote" ? n.at?.source : n.at[0]?.source) ?? source;
  const pick = (n: QuoteNote | SummaryNote) => { setSelected(n.id); setSource(sourceOf(n)); };
  const pickByNote = (id: string) => { setSelected(id); document.querySelector<HTMLElement>(`[data-card="${id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }); };
  const current = texts[source];
  const highlights = highlightsFor(notes, source, selected);
  const judgement = f.ai_status === "Evidence only";
  const slots = (kind: "quote" | "summary") => {
    const list = kind === "quote" ? notes.quotes : notes.summaries;
    const max = kind === "quote" ? MAX_QUOTES : MAX_SUMMARIES;
    return Array.from({ length: max }, (_, i) => {
      const n = list[i];
      return n ? <div key={n.id} data-card={n.id}><Card note={n} selected={selected === n.id} onSelect={() => pick(n)} sourceLabel={label(sourceOf(n))} /></div> : <Slot key={`e${i}`} n={i + 1} kind={kind} />;
    });
  };

  return (
    <div className="rv-viewer-wrap">
      <div className="rv-backdrop" onClick={onClose} aria-hidden="true" />
      <section className="rv-viewer" aria-label={`${CRITERIA[f.rule_code] ?? plainRule(f)}: document and AI notes`}>
        <header className="rv-viewer-head">
          <div ref={head} tabIndex={-1}>
            <p className="eyebrow">Merit criterion</p>
            <h2>{CRITERIA[f.rule_code] ?? plainRule(f)}</h2>
            <div className="rv-chips"><StatusChip status={effectiveStatus(f)} /><span className="rv-tag">{judgement ? "Officer judgement required. No AI score or suggestion." : CHECKED_BY_LABEL[checkedBy(f)]}</span></div>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close (Esc)"><Icon name="close" /></button>
        </header>
        <div className="rv-viewer-body">
          <div className="rv-doc">
            {tabs.length > 1 && (
              <div className="rv-tabs" role="tablist" aria-label="Documents for this criterion">
                {tabs.map((k) => <button key={k} role="tab" aria-selected={k === source} className={k === source ? "active" : ""} onClick={() => setSource(k)}>{label(k)}</button>)}
              </div>
            )}
            {tabs.length <= 1 && current && <p className="rv-doc-title">{current.label}</p>}
            {current ? <TextView text={current.text} highlights={highlights} selected={selected} onPick={pickByNote} />
              : <p className="empty-state">The text could not be loaded. Please read the original application.</p>}
          </div>
          <aside className="rv-notes" aria-label="AI notes">
            <h3>AI notes</h3>
            <p className="muted">Passages the AI pointed at, found in the text by code and shown in the applicant’s own words. Wording, spelling and English level are not assessed here.</p>
            <div className="rv-legend"><span className="rv-key quote" />Verified quote <span className="rv-key summary" />AI summary</div>
            <h4>Quotes</h4>
            {slots("quote")}
            {notes.moreQuotes > 0 && <p className="muted">{notes.moreQuotes} more passage{notes.moreQuotes === 1 ? "" : "s"} not shown here. Read the document on the left.</p>}
            <h4>AI summaries</h4>
            {slots("summary")}
            {notes.unlinked.length > 0 && (
              <div className="rv-failed"><h4>Could not be linked to the text</h4>
                <p className="muted">The AI wrote these without a passage code could find. Do not rely on them.</p>
                {notes.unlinked.map((n) => <p key={n.id} className="rv-failed-item">{n.text}</p>)}</div>
            )}
            {notes.unverified.length > 0 && (
              <div className="rv-failed"><h4>Could not verify</h4>
                <p className="muted">Code did not find these quotes in the text, so they are not shown as quotes and not highlighted.</p>
                {notes.unverified.map((n) => <p key={n.id} className="rv-failed-item">Could not verify a quote{n.label ? ` (${n.label})` : ""}.</p>)}</div>
            )}
            <div className="rv-assess">
              <h3>Your assessment</h3>
              {f.latest_review && f.latest_review.action !== "ask_applicant" && (
                <p className="decision-line">Recorded {formatDate(f.latest_review.reviewed_at, true)}: <strong>{CHOICES.find((c) => c.status === f.latest_review!.final_status)?.label ?? f.latest_review.final_status}</strong></p>
              )}
              {judgement
                ? <AssessForm f={f} locked={locked} onChanged={onChanged} />
                : <RuleDecision f={f} locked={locked} busy={busy} onDialog={onDialog} onConfirm={onConfirm} />}
            </div>
            <div className="rv-pfoot">
              <button className="link-button" onClick={() => onTrace(`rule:${f.rule_code}`)}>See how this was redacted and how the AI read it <Icon name="arrow" size={14} /></button>
            </div>
          </aside>
        </div>
      </section>
    </div>
  );
}

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

/** Merit rows that are calculated by code (the short answer's length): the usual confirm / override / ask. */
function RuleDecision({ f, locked, busy, onDialog, onConfirm }: { f: Finding; locked: boolean; busy: boolean; onDialog: (d: DialogState) => void; onConfirm: (f: Finding) => void }) {
  if (locked) return <p className="muted">This application is signed off. Decisions are locked.</p>;
  return (
    <>
      <p className="rv-reason"><strong>Short reason:</strong> {f.rationale ?? "No explanation was given."}</p>
      <div className="rule-actions">
        <Button icon="check" disabled={busy || !f.is_valid || f.error_flag} onClick={() => f.ai_status === "Not met" ? onDialog({ kind: "confirm-not-met", finding: f }) : onConfirm(f)}>Confirm {f.ai_status.toLowerCase()}</Button>
        <Button variant="secondary" icon="edit" disabled={busy} onClick={() => onDialog({ kind: "override", finding: f })}>Override</Button>
        <Button variant="quiet" icon="send" disabled={busy} onClick={() => onDialog({ kind: "ask", finding: f })}>Ask applicant</Button>
      </div>
    </>
  );
}
