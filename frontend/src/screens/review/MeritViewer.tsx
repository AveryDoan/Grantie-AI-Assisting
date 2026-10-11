// Merit criterion viewer. Left: the AI summary bullets (in the order they appear in the document), then the source text,
// collapsed when long, with the highlighted passage. Right: other passages, anything code could not link or verify, and the
// officer's own mark. Every bullet points at a passage code found in the text; clicking it shows that passage highlighted in the
// applicant's own words. A bullet with no source is listed apart. No strength, quality or ranking cue appears anywhere.
import { useEffect, useMemo, useRef, useState } from "react";
import type { Detail, Finding } from "../../api";
import { Icon, StatusChip, effectiveStatus } from "../../ui";
import type { DialogState } from "./Dialogs";
import { collapsedBlocks, forMode, highlightsFor, notesOf, sourcesOf, whereIs, type QuoteNote, type SummaryNote } from "./highlight";
import { MarkControl } from "./MarkControl";
import { docForSource, pdfTargets, type EvidenceSpan } from "./pdfTargets";
import { PdfViewer, type PdfItem } from "./PdfViewer";
import { CHECKED_BY_LABEL, checkedBy, plainRule } from "./model";
import { TextView } from "./TextView";

export const CRITERIA: Record<string, string> = { M1: "Academic Merit", M2: "Supporting Evidence", M3: "Leadership", M4: "Community Engagement", M5: "Short answer" };
const SUMMARY_LABEL = "AI summary, check the original";

export function MeritViewer({ f, detail, mode = "criterion", locked, onClose, onChanged, onTrace }: {
  f: Finding; detail: Detail; mode?: "criterion" | "referee"; locked: boolean; busy?: boolean; onClose: () => void; onChanged: () => void;
  onTrace: (item: string) => void; onDialog?: (d: DialogState) => void; onConfirm?: (f: Finding) => void;
}) {
  const texts = detail.source_texts ?? {};
  const order = useMemo(() => Object.keys(texts), [texts]);
  const notes = useMemo(() => notesOf(forMode(f, mode), order), [f, mode, order]);
  const tabs = useMemo(() => sourcesOf(notes).filter((k) => texts[k]), [notes, texts]);
  const [source, setSource] = useState(tabs[0] ?? order[0] ?? "application_text");
  const [selected, setSelected] = useState<string | null>(null);
  const [full, setFull] = useState(false);
  const [mode_, setMode] = useState<"text" | "pdf">("pdf");
  const head = useRef<HTMLDivElement>(null);
  useEffect(() => { head.current?.focus(); }, [f.id, mode]);

  const label = (k: string) => texts[k]?.label ?? "Application form";
  const sourceOf = (n: QuoteNote | SummaryNote) => (n.kind === "quote" ? n.at?.source : n.at[0]?.source) ?? source;
  const startOf = (n: QuoteNote | SummaryNote) => (n.kind === "quote" ? n.at?.start : n.at[0]?.start);
  const where = (n: QuoteNote | SummaryNote) => {
    const src = sourceOf(n);
    const start = startOf(n);
    const w = start !== undefined && texts[src] ? whereIs(texts[src].text, start) : null;
    return `${label(src)}${w ? ` · page ${w.page}, paragraph ${w.paragraph}` : ""}`;
  };
  const pick = (n: QuoteNote | SummaryNote) => { setSelected(n.id); setSource(sourceOf(n)); };
  const pickByNote = (id: string) => { setSelected(id); document.querySelector<HTMLElement>(`[data-card="${id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }); };

  const current = texts[source];
  const highlights = highlightsFor(notes, source, selected);
  const view = current ? collapsedBlocks(current.text, highlights) : null;
  const collapsed = !!view?.collapsible && !full;
  const title = mode === "referee" ? "Referee check" : (CRITERIA[f.rule_code] ?? plainRule(f));

  // Evidence on this source as highlights on its PDF: the bullets' passages (blue) and the other verified passages (yellow).
  const pdfDoc = docForSource(detail, source);
  const spanText = (a: { source: string; start: number; end: number }) => texts[a.source]?.text.slice(a.start, a.end);
  const evidence: EvidenceSpan[] = [
    ...notes.bullets.flatMap((b) => b.at.filter((a) => a.source === source).map((a, i) => ({ id: `${b.id}~${i}`, group: b.id, kind: "summary" as const, source, start: a.start, end: a.end, text: spanText(a), label: "AI summary source" }))),
    ...notes.others.filter((q) => q.at && q.at.source === source).map((q) => ({ id: q.id, group: q.id, kind: "quote" as const, source, start: q.at!.start, end: q.at!.end, text: spanText(q.at!), label: "Verified quote" })),
  ] as (EvidenceSpan & { group: string })[];
  const pdfItems: PdfItem[] = pdfTargets(detail, evidence)[0]?.items.map((it, i) => ({ ...it, group: (evidence[i] as { group?: string }).group })) ?? [];

  const NoteCard = ({ n }: { n: QuoteNote }) => (
    <div data-card={n.id}>
      <button className={`rv-note quote ${selected === n.id ? "sel" : ""}`} aria-pressed={selected === n.id} onClick={() => pick(n)} disabled={n.state === "unlocated"}>
        <span className="rv-note-label">Verified quote</span>
        {n.label && <small className="rv-note-sub">{n.label}</small>}
        <span className="rv-note-text">“{n.text}”</span>
        {n.state === "unlocated"
          ? <small className="rv-note-warn"><Icon name="question" size={14} />Could not locate in the document</small>
          : <small className="rv-note-act"><Icon name="search" size={14} />Show in document · {where(n)}</small>}
      </button>
    </div>
  );

  return (
    <div className="rv-viewer-wrap">
      <div className="rv-backdrop" onClick={onClose} aria-hidden="true" />
      <section className="rv-viewer" aria-label={`${title}: summary, source text and mark`}>
        <header className="rv-viewer-head">
          <div ref={head} tabIndex={-1}>
            <p className="eyebrow">{mode === "referee" ? "Is the referee identified and contactable?" : "Merit criterion"}</p>
            <h2>{title}</h2>
            <div className="rv-chips">
              {mode === "criterion" && f.ai_status === "Evidence only" ? <StatusChip status="Evidence only" /> : mode === "criterion" ? <StatusChip status={effectiveStatus(f)} /> : null}
              <span className="rv-tag">{mode === "referee" ? "Check only. Not marked." : f.ai_status === "Evidence only" ? "Officer judgement" : CHECKED_BY_LABEL[checkedBy(f)]}</span>
            </div>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close (Esc)"><Icon name="close" /></button>
        </header>
        <div className="rv-viewer-body three">
          <div className="rv-pane rv-pane-doc" role="region" aria-label="Original document">
            <h3 className="rv-pane-title">Original document</h3>
            {tabs.length > 1 && (
              <div className="rv-tabs" role="tablist" aria-label="Documents for this criterion">
                {tabs.map((k) => <button key={k} role="tab" aria-selected={k === source} className={k === source ? "active" : ""} onClick={() => { setSource(k); setFull(false); }}>{label(k)}</button>)}
              </div>
            )}
            {pdfDoc && (
              <div className="rv-viewtoggle" role="group" aria-label="How to read the source">
                <button className={mode_ === "pdf" ? "active" : ""} aria-pressed={mode_ === "pdf"} onClick={() => setMode("pdf")}>Original PDF</button>
                <button className={mode_ === "text" ? "active" : ""} aria-pressed={mode_ === "text"} onClick={() => setMode("text")}>Text</button>
              </div>
            )}
            {pdfDoc && mode_ === "pdf" ? (
              <PdfViewer docId={pdfDoc.id} appId={detail.application.id} items={pdfItems} selectedId={selected} onSelect={pickByNote} height={760} />
            ) : current ? (
              <>
                <div className="rv-srchead">
                  <strong>{current.label}</strong>
                  {view?.collapsible && <button className="link-button" onClick={() => setFull(!full)} aria-expanded={full}>{full ? "Show less" : `Show full text (${view.words} words)`}</button>}
                </div>
                {collapsed && view && view.blocks.length === 0 && <p className="muted">Long text, collapsed. Choose a bullet to see its passage, or show the full text.</p>}
                <TextView text={current.text} highlights={highlights} selected={selected} onPick={pickByNote} only={collapsed ? view!.blocks : undefined} />
              </>
            ) : <p className="empty-state">The text could not be loaded. Please read the original application.</p>}
          </div>
          <div className="rv-pane rv-pane-ai" role="region" aria-label="AI summary">
            <h3 className="rv-pane-title">AI summary</h3>
            {notes.bullets.length > 0 ? (
              <>
                <ul className="rv-bulletlist">
                  {notes.bullets.map((b) => (
                    <li key={b.id} data-card={b.id}>
                      <button className={`rv-bullet ${selected === b.id ? "sel" : ""}`} aria-pressed={selected === b.id} onClick={() => pick(b)} disabled={b.state === "unlocated"}>
                        <span className="rv-bullet-label">{SUMMARY_LABEL}</span>
                        <span className="rv-bullet-text">{b.text}</span>
                        {b.state === "unlocated"
                          ? <small className="rv-note-warn"><Icon name="question" size={14} />Could not locate in the document</small>
                          : <small className="rv-note-act"><Icon name="search" size={14} />{where(b)}</small>}
                      </button>
                    </li>
                  ))}
                </ul>
                {notes.moreBullets > 0 && <p className="muted">{notes.moreBullets} more point{notes.moreBullets === 1 ? "" : "s"} are in the text.</p>}
              </>
            ) : <p className="empty-state">There is no AI summary for this item. Read the original document.</p>}
            {mode === "referee" && notes.others.length > 0 && <><h4>Referee details found</h4>{notes.others.map((n) => <NoteCard key={n.id} n={n} />)}</>}
            {mode === "criterion" && notes.others.length > 1 && <><h4>Other passages</h4>{notes.others.map((n) => <NoteCard key={n.id} n={n} />)}</>}
            {mode === "criterion" && notes.others.length === 1 && <NoteCard n={notes.others[0]} />}
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
            <div className="rv-pfoot">
              <button className="link-button" onClick={() => onTrace(`rule:${f.rule_code}`)}>See how this was redacted and how the AI read it <Icon name="arrow" size={14} /></button>
            </div>
          </div>
          <aside className="rv-pane rv-pane-mark" aria-label="Your mark">
            {mode === "criterion" ? (
              <div className="rv-assess">
                <h3 className="rv-pane-title">Your mark</h3>
                {f.rationale && f.ai_status !== "Evidence only" && <p className="muted">Length check, by code: {f.rationale}</p>}
                <p className="muted">Your mark, 0 to 100.</p>
                <MarkControl f={f} applicationId={detail.application.id} locked={locked} onChanged={onChanged} />
              </div>
            ) : <p className="muted">The referee check is a check only. It is not marked.</p>}
          </aside>
        </div>
      </section>
    </div>
  );
}
