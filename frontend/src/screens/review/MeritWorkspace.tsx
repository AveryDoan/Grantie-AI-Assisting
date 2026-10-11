// The grading workspace for one merit criterion. Left: the documents. Right: the AI summary, then the officer's own mark.
// The summary comes first and is calm: an overview, then a card per theme with short plain sentences. Quotes appear only
// when the officer asks ("Show in document"), as highlights on the page and as "Verified quote" cards in the lane.
// Order is the theme template, then document order, for every applicant. No colour, order or word says how strong anything is.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Detail, type Finding, type MeritQuote, type MeritSummary, type PdfLocation } from "../../api";
import { Button, Icon } from "../../ui";
import { whereIs, type Highlight } from "./highlight";
import { MarkControl } from "./MarkControl";
import { documentTabs, focusAtQuote, focusItem, focusTheme, itemForQuote, move, quotesByDocument, type LaneState, type Step } from "./meritNav";
import { FIELD_PREFIX, docForSource, pdfTargets, type EvidenceSpan } from "./pdfTargets";
import { PdfViewer } from "./PdfViewer";
import { TextView } from "./TextView";

const FORM = "application_text";

export function MeritWorkspace({ f, title, detail, summary, locked, onClose, onChanged, onTrace }: {
  f: Finding; title: string; detail: Detail; summary: MeritSummary; locked: boolean;
  onClose: () => void; onChanged: () => void; onTrace: (item: string) => void;
}) {
  const texts = detail.source_texts ?? {};
  const tabs = useMemo(() => documentTabs(summary).filter((t) => texts[t.source] || docForSource(detail, t.source)), [summary, texts, detail]);
  const [tab, setTab] = useState(tabs[0]?.source ?? FORM);
  const [view, setView] = useState<"pdf" | "text">("pdf");
  const [lane, setLane] = useState<LaneState>({ kind: "summary" });
  const [located, setLocated] = useState<Record<string, PdfLocation>>({});
  const [menu, setMenu] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const saved = useRef<{ top: number; focus: string | null }>({ top: 0, focus: null });
  const head = useRef<HTMLDivElement>(null);
  useEffect(() => { head.current?.focus(); }, []);

  const steps: Step[] = lane.kind === "focus" ? lane.steps : [];
  const at = lane.kind === "focus" ? lane.at : 0;
  const current = steps[at] ?? null;

  // The viewer follows the quote: switch to its document when Next or Previous crosses into another one.
  useEffect(() => { if (current) setTab(current.quote.source); }, [current?.quote.source, at, lane.kind]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setLocated({}); }, [lane]);

  const open = (next: LaneState, from?: string) => {
    saved.current = { top: scroller.current?.scrollTop ?? 0, focus: from ?? null };
    setMenu(false);
    setLane(next);
    if (scroller.current) scroller.current.scrollTop = 0;
  };
  const back = () => {
    setLane({ kind: "summary" });
    setTimeout(() => {
      if (scroller.current) scroller.current.scrollTop = saved.current.top;
      if (saved.current.focus) document.querySelector<HTMLElement>(`[data-show="${CSS.escape(saved.current.focus)}"]`)?.focus();
    }, 0);
  };
  const step = (delta: number) => setLane((l) => move(l, delta));

  // Keyboard: Esc goes back to the summary (and only then closes the workspace); left and right arrows step through quotes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.tagName === "SELECT");
      if (e.key === "Escape" && lane.kind !== "summary") { e.preventDefault(); e.stopImmediatePropagation(); back(); return; }
      if (lane.kind === "focus" && !typing && (e.key === "ArrowLeft" || e.key === "ArrowRight")) { e.preventDefault(); step(e.key === "ArrowRight" ? 1 : -1); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  });

  // What the viewer highlights: the quotes of the open line on this document. The current one is strong, the rest light.
  const pdfDoc = docForSource(detail, tab);
  const evidence: EvidenceSpan[] = steps.map((s, i) => ({ id: `q${i}`, kind: "quote" as const, source: s.quote.source, start: s.quote.span?.start,
    end: s.quote.span?.end, text: s.quote.text, label: "Verified quote" })).filter((e) => pdfDoc && docForSource(detail, e.source)?.id === pdfDoc.id);
  const items = pdfDoc ? (pdfTargets(detail, evidence)[0]?.items ?? []).map((it) => ({ ...it, group: it.id })) : [];
  const highlights: Highlight[] = steps.flatMap((s, i) => (s.quote.span && s.quote.span.source === tab ? [{ id: `q${i}`, noteId: `q${i}`, start: s.quote.span.start, end: s.quote.span.end, kind: "quote" as const }] : []));
  const pick = (id: string) => {
    const i = Number(id.slice(1));
    if (lane.kind === "focus" && Number.isInteger(i)) setLane({ ...lane, at: i });
  };

  const loc = current ? located[`q${at}`] : undefined;
  const page = loc?.rects[0]?.page ?? (current?.quote.span && texts[current.quote.source] ? whereIs(texts[current.quote.source].text, current.quote.span.start)?.page : undefined);
  const notPlaced = !!current && (view === "pdf" ? (!pdfDoc || (loc ? loc.status === "not_found" || !loc.rects.length : !current.quote.span && !current.quote.text)) : !current.quote.span);
  const jump = () => {
    if (!current) return;
    setTab(current.quote.source);
    setTimeout(() => document.querySelector(`.mw [data-hlg="q${at}"], .mw mark[data-note="q${at}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" }), 60);
  };
  const rerun = async () => { setMenu(false); setRerunning(true); try { await api.regenerateMeritSummary(detail.application.id, f.rule_code); onChanged(); } finally { setRerunning(false); } };
  const fromHighlight = (q: MeritQuote) => { const it = itemForQuote(summary, q); return it ? focusItem(it) : { kind: "focus" as const, title: "Passage found in the documents", steps: [{ quote: q, itemId: "" }], at: 0 }; };
  const noAi = summary.status !== "ok" || summary.themes.length === 0;

  return (
    <div className="rv-viewer-wrap">
      <div className="rv-backdrop" onClick={onClose} aria-hidden="true" />
      <section className="rv-viewer mw" aria-label={`${title}: documents, AI summary and your mark`}>
        <header className="rv-viewer-head">
          <div ref={head} tabIndex={-1} className="mw-title">
            <h2>{title}</h2>
            <span className="mw-chip" title="Evidence only. You decide and mark." tabIndex={0} aria-label="Officer judgement: evidence only. You decide and mark.">
              <Icon name="eye" size={14} />Officer judgement</span>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close (Esc)"><Icon name="close" /></button>
        </header>
        <div className="mw-body">
          <div className="mw-doc" role="region" aria-label="Documents">
            <div className="mw-tabs" role="tablist" aria-label="Documents">
              {tabs.map((t) => (
                <button key={t.source} role="tab" aria-selected={t.source === tab} className={t.source === tab ? "on" : ""} title={t.file_name ?? undefined} onClick={() => setTab(t.source)}>{t.name}</button>
              ))}
            </div>
            <div className="rv-viewtoggle" role="group" aria-label="How to read the document">
              <button className={view === "pdf" ? "active" : ""} aria-pressed={view === "pdf"} onClick={() => setView("pdf")} disabled={!pdfDoc}>Original PDF</button>
              <button className={view === "text" ? "active" : ""} aria-pressed={view === "text"} onClick={() => setView("text")}>Text</button>
            </div>
            {view === "pdf" && pdfDoc ? (
              <div className="mw-viewer">
                <PdfViewer docId={pdfDoc.id} appId={detail.application.id} items={items} selectedId={current ? `q${at}` : null}
                  onSelect={(id) => { if (lane.kind === "focus") pick(id); }} onLocated={(r) => setLocated((x) => ({ ...x, ...r }))} height="100%" />
              </div>
            ) : texts[tab] ? (
              <div className="mw-text"><TextView text={texts[tab].text} highlights={highlights} selected={current ? `q${at}` : null} onPick={pick} /></div>
            ) : <p className="empty-state">This document has no text to show. Open the original PDF.</p>}
          </div>

          <div className="mw-lane">
            <div className="mw-scroll" ref={scroller}>
              {lane.kind === "summary" && (
                <>
                  <header className="mw-head">
                    <div>
                      <h3>{summary.label ?? title}</h3>
                      <p className="mw-muted">Read from {summary.coverage.with_matches} of {summary.coverage.document_count} documents</p>
                      <p className="mw-ai">AI summary. Check the original.</p>
                    </div>
                    <div className="mw-menu">
                      <button className="icon-button" aria-label="More actions" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(!menu)}>⋯</button>
                      {menu && (
                        <div role="menu" className="mw-menu-list">
                          <button role="menuitem" onClick={() => open({ kind: "all" })}>Show all quotes</button>
                          <button role="menuitem" disabled={locked || rerunning} onClick={() => void rerun()}>{rerunning ? "Reading the documents…" : "Run again"}</button>
                          <button role="menuitem" onClick={() => open({ kind: "other" })}>Other passages found</button>
                          <button role="menuitem" onClick={() => open({ kind: "failed" })}>Could not verify</button>
                        </div>
                      )}
                    </div>
                  </header>
                  {noAi && <p className="mw-note">No AI summary is available. Choose “Show all quotes” in the ⋯ menu to read what was found. You can still mark.</p>}
                  {summary.themes.map((t) => (
                    <section className="mw-theme" key={t.id} aria-label={t.label}>
                      <header>
                        <h4>{t.label}</h4>
                        <button className="link-button" data-show={`theme:${t.id}`} onClick={() => open(focusTheme(t), `theme:${t.id}`)}>Show all in document</button>
                      </header>
                      {!(t.from.length === 1 && t.label.startsWith(t.from[0].name)) && (
                        <p className="mw-from">From: {t.from.map((d, i) => <span key={d.source} title={d.file_name ?? undefined}>{d.name}{i < t.from.length - 1 ? ", " : ""}</span>)}</p>
                      )}
                      <ul>
                        {t.items.map((it) => (
                          <li key={it.item_id}>
                            <button className="mw-bullet" data-show={it.item_id} onClick={() => open(focusItem(it), it.item_id)}
                              title="Show in document" aria-label={`${it.text ?? ""}. Show in document`}>
                              <span>{it.text}{it.differs && <em className="mw-differs"> Documents differ: both are shown, nothing was chosen.</em>}</span>
                              <Icon name="search" size={15} />
                            </button>
                          </li>
                        ))}
                      </ul>
                    </section>
                  ))}
                </>
              )}

              {lane.kind === "focus" && (
                <div className="mw-focus">
                  <button className="link-button mw-back" onClick={back}><Icon name="left" size={14} />Back to summary</button>
                  <blockquote className="mw-line">{lane.title}</blockquote>
                  {current ? (
                    <>
                      <div className="mw-stepper" aria-live="polite">
                        <span>Quote {at + 1} of {steps.length}, {current.quote.name}{page ? `, page ${page}` : ""}</span>
                        <span className="mw-stepper-btns">
                          <Button variant="secondary" disabled={at === 0} onClick={() => step(-1)}>Previous</Button>
                          <Button variant="secondary" disabled={at === steps.length - 1} onClick={() => step(1)}>Next</Button>
                        </span>
                      </div>
                      <figure className="mw-quote">
                        <figcaption>Verified quote</figcaption>
                        <blockquote>“{plain(current.quote.text)}”</blockquote>
                        {notPlaced
                          ? <p className="mw-missing">Highlight not available for this quote. The exact text is shown here.</p>
                          : <button className="link-button" onClick={jump}>Jump to highlight</button>}
                      </figure>
                    </>
                  ) : <p className="mw-note">No quote is attached to this line.</p>}
                </div>
              )}

              {lane.kind === "all" && (
                <div className="mw-focus">
                  <button className="link-button mw-back" onClick={back}><Icon name="left" size={14} />Back to summary</button>
                  <h3>All quotes</h3>
                  {(summary.themes.length ? quotesByDocument(summary) : groupPassages(summary.all_quotes)).map((g) => (
                    <section key={g.doc.source} className="mw-docgroup">
                      <h4 title={g.doc.file_name ?? undefined}>{g.doc.name}</h4>
                      <ol>
                        {g.quotes.map(({ quote, item }) => {
                          const pg = quote.span && texts[quote.source] ? whereIs(texts[quote.source].text, quote.span.start)?.page : undefined;
                          return (
                            <li key={`${quote.source}-${quote.span?.start ?? quote.text}`} className="mw-quote">
                              <blockquote>“{plain(quote.text)}”</blockquote>
                              <span className="mw-quote-foot">
                                {pg ? <small>Page {pg}</small> : null}
                                <button className="link-button" onClick={() => open(item ? focusAtQuote(item, quote) : fromHighlight(quote))}>Jump to highlight</button>
                                {item && <button className="link-button" onClick={() => open(focusItem(item))}>See the summary line</button>}
                              </span>
                            </li>
                          );
                        })}
                      </ol>
                    </section>
                  ))}
                </div>
              )}

              {lane.kind === "other" && (
                <div className="mw-focus">
                  <button className="link-button mw-back" onClick={back}><Icon name="left" size={14} />Back to summary</button>
                  <h3>Other passages found</h3>
                  <p className="mw-muted">Found in the documents, but no summary line uses them.</p>
                  {summary.other_passages.length === 0 ? <p className="mw-note">None. Every passage is used in the summary.</p> : (
                    <ol>{summary.other_passages.map((q) => (
                      <li key={q.passage_id} className="mw-quote"><small>{q.name}</small><blockquote>“{plain(q.text)}”</blockquote>
                        <button className="link-button" onClick={() => open({ kind: "focus", title: "Passage found in the documents", steps: [{ quote: q, itemId: "" }], at: 0 })}>Jump to highlight</button></li>
                    ))}</ol>
                  )}
                </div>
              )}

              {lane.kind === "failed" && (
                <div className="mw-focus">
                  <button className="link-button mw-back" onClick={back}><Icon name="left" size={14} />Back to summary</button>
                  <h3>Could not verify</h3>
                  <p className="mw-muted">These were left out because the check did not pass. Nothing from them is shown anywhere.</p>
                  {summary.could_not_verify.length === 0 ? <p className="mw-note">Nothing was left out.</p> : (
                    <ul className="mw-failed">{tally(summary.could_not_verify.map((d) => d.reason)).map(([r, n]) => <li key={r}>{r} <small>({n})</small></li>)}</ul>
                  )}
                </div>
              )}
              <p className="mw-trace"><button className="link-button" onClick={() => onTrace(`rule:${f.rule_code}`)}>See how this was redacted and how the AI read it <Icon name="arrow" size={14} /></button></p>
            </div>
            <div className="mw-mark" aria-label="Your mark">
              <h3>Your mark</h3>
              <MarkControl f={f} applicationId={detail.application.id} locked={locked} onChanged={onChanged} />
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

const plain = (t: string | null) => (t ?? "").replace(FIELD_PREFIX, "");

function groupPassages(all: MeritQuote[]) {
  const m = new Map<string, { doc: { source: string; name: string; file_name: string | null }; quotes: { quote: MeritQuote; item: null }[] }>();
  for (const q of all) {
    const g = m.get(q.source) ?? { doc: { source: q.source, name: q.name, file_name: q.file_name }, quotes: [] };
    g.quotes.push({ quote: q, item: null });
    m.set(q.source, g);
  }
  return [...m.values()];
}

function tally(xs: string[]): [string, number][] {
  const m = new Map<string, number>();
  xs.forEach((x) => m.set(x, (m.get(x) ?? 0) + 1));
  return [...m.entries()];
}
