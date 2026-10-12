// Redaction check: the original and the redacted text side by side. The right pane is exactly what the AI sees. The original on the
// left is masked ON THE SERVER until the officer switches it on (that is logged once); a blurred original is never sent to the browser.
// Every redacted span is outlined in Redaction violet on both sides with a type chip; hovering or clicking one highlights its match on
// the other side and scrolls both panes. Scrolling is linked. J / K move between spans. Mobile shows tabs instead of two panes.
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { api, type CompareSpan, type CompareText } from "../../api";
import { Button, ErrorNotice, Icon, Loading } from "../../ui";
import { usePersisted } from "../../persisted";
import { MarkPersonal } from "./MarkPersonal";
import { PdfViewer } from "../review/PdfViewer";

function Pane({ text, spans, side, active, setActive, refEl, onScroll, shown }: {
  text: string; spans: CompareSpan[]; side: "orig" | "red"; active: string | null; setActive: (id: string | null) => void;
  refEl: RefObject<HTMLDivElement | null>; onScroll: () => void; shown: boolean;
}) {
  const nodes: ReactNode[] = [];
  let at = 0;
  const idx = side === "orig" ? "orig" : "red";
  for (const s of spans) {
    const [a, b] = s[idx];
    if (a < at || b > text.length) continue;
    nodes.push(text.slice(at, a));
    nodes.push(
      <mark key={s.id} data-span={s.id} className={`cv-span ${active === s.id ? "on" : ""}`} onMouseEnter={() => setActive(s.id)} onClick={() => setActive(s.id)}>
        <span className="cv-chip" aria-hidden="true">{s.type}</span>{text.slice(a, b)}
      </mark>,
    );
    at = b;
  }
  nodes.push(text.slice(at));
  return (
    <div className={`cv-pane ${side === "orig" && !shown ? "masked" : ""}`} ref={refEl} onScroll={onScroll} tabIndex={0}
      aria-label={side === "orig" ? (shown ? "Original text" : "Original text, hidden until you switch it on") : "Redacted text, exactly what the AI sees"}>
      <pre>{nodes}</pre>
    </div>
  );
}

export function CompareViewer({ appId, source, title, onClose, onChanged }: { appId: string; source: string; title: string; onClose: () => void; onChanged: () => void }) {
  const [data, setData] = useState<CompareText | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [reveal, setReveal] = useState(false);
  const [active, setActive] = useState<string | null>(null);
  const [type, setType] = usePersisted("compare.type", "");
  const [tab, setTab] = useState<"orig" | "red">("orig");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [marking, setMarking] = useState<string | null>(null);
  const isPdf = source.startsWith("document:") && /\.pdf$/i.test(title);
  const [asText, setAsText] = useState(false);          // PDFs show the pages by default, like the PDF viewer; text is one click away
  const [ratio, setRatio] = useState<{ from: "l" | "r"; v: number } | null>(null);
  const pdfMode = isPdf && !asText;
  const left = useRef<HTMLDivElement>(null);
  const right = useRef<HTMLDivElement>(null);
  const syncing = useRef(false);

  const load = useCallback((r: boolean) => {
    api.compareText(appId, source, r).then(setData, setError);
  }, [appId, source]);
  useEffect(() => { load(false); }, [load]);

  const spans = useMemo(() => (data?.spans ?? []).filter((s) => !type || s.type === type), [data, type]);
  const at = Math.max(0, spans.findIndex((s) => s.id === active));
  const go = useCallback((d: number) => {
    if (!spans.length) return;
    const n = spans[(at + d + spans.length) % spans.length];
    setActive(n.id);
  }, [spans, at]);

  // scroll both panes to the active span
  useEffect(() => {
    if (!active) return;
    syncing.current = true;
    for (const ref of [left, right]) {
      const el = ref.current?.querySelector<HTMLElement>(`[data-span="${CSS.escape(active)}"]`);
      if (el && ref.current) ref.current.scrollTo({ top: el.offsetTop - ref.current.clientHeight / 2, behavior: "smooth" });
    }
    setTimeout(() => { syncing.current = false; }, 400);
  }, [active]);
  const link = (from: RefObject<HTMLDivElement | null>, to: RefObject<HTMLDivElement | null>) => () => {
    if (syncing.current || !from.current || !to.current) return;
    syncing.current = true;
    const f = from.current, t = to.current;
    t.scrollTop = (f.scrollTop / Math.max(1, f.scrollHeight - f.clientHeight)) * (t.scrollHeight - t.clientHeight);
    requestAnimationFrame(() => { syncing.current = false; });
  };

  useEffect(() => {
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.key === "Escape" && !marking) onClose();
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      if (e.key === "j" || e.key === "J") go(1);
      if (e.key === "k" || e.key === "K") go(-1);
    };
    document.addEventListener("keydown", onKey);
    return () => { document.body.style.overflow = ""; document.removeEventListener("keydown", onKey); };
  }, [go, onClose, marking]);

  const selection = (): string => {
    const sel = window.getSelection();
    const inside = (n: Node | null) => !!n && (left.current?.contains(n) || right.current?.contains(n));
    return sel && inside(sel.anchorNode) ? sel.toString().trim() : "";
  };
  const turnOn = () => { setReveal(true); load(true); };      // load(true) writes the one audit entry
  const turnOff = () => { setReveal(false); load(false); };
  const keep = async () => {
    if (!active || !note.trim()) return;
    setBusy(true); setError(null);
    try { await api.unmaskItem(appId, active, note.trim()); setNote(""); setActive(null); load(reveal && false); setReveal(false); onChanged(); }
    catch (e) { setError(e); }
    setBusy(false);
  };
  const download = () => {
    if (!data) return;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([data.redacted], { type: "text/plain" }));
    a.download = `redacted-${title}.txt`; a.click(); URL.revokeObjectURL(a.href);
  };
  const cur = spans.find((s) => s.id === active);

  return createPortal(
    <div className="cv-wrap" role="dialog" aria-modal="true" aria-labelledby="cv-title">
      <header className="cv-top">
        <div><h2 id="cv-title">Compare: {title}</h2><small>{data ? `${data.spans.length} redacted ${data.spans.length === 1 ? "span" : "spans"}` : ""}</small></div>
        <div className="cv-tools">
          <label className="cv-filter"><span className="sr-only">Filter by type</span>
            <select value={type} onChange={(e) => setType(e.target.value)}><option value="">All types</option>{(data?.types ?? []).map((t) => <option key={t}>{t}</option>)}</select></label>
          <Button variant="secondary" icon="left" onClick={() => go(-1)} disabled={!spans.length}>Previous <kbd>K</kbd></Button>
          <Button variant="secondary" icon="arrow" onClick={() => go(1)} disabled={!spans.length}>Next <kbd>J</kbd></Button>
          <Button variant="quiet" icon="download" onClick={download}>Download redacted copy</Button>
          <button className="icon-button" onClick={onClose} aria-label="Close"><Icon name="close" /></button>
        </div>
      </header>
      {error ? <div className="cv-msg"><ErrorNotice error={error} /></div> : !data ? <div className="cv-msg"><Loading /></div> : !data.has_text ? (
        <div className="cv-msg"><p className="notice"><Icon name="info" />No text layer, nothing to redact or compare on this page.</p></div>
      ) : (
        <>
          <div className="cv-tabs" role="tablist" aria-label="Which text">
            <button role="tab" aria-selected={tab === "orig"} className={tab === "orig" ? "on" : ""} onClick={() => setTab("orig")}>Original</button>
            <button role="tab" aria-selected={tab === "red"} className={tab === "red" ? "on" : ""} onClick={() => setTab("red")}>Redacted</button>
          </div>
          <div className="cv-bar">
            <label className="cv-switch"><input type="checkbox" checked={reveal} onChange={(e) => (e.target.checked ? turnOn() : turnOff())} />
              <span>Show original text <small>(this is logged)</small></span></label>
            <span className="cv-legend"><Icon name="shield" size={14} />Right: exactly what the AI would see.</span>
            {isPdf && <button className="link-button" aria-pressed={asText} onClick={() => setAsText(!asText)}>{asText ? "View the pages" : "View as text"}</button>}
            {pdfMode ? <small className="muted">To mark a missed item, use “View as text”.</small> : <Button variant="secondary" onClick={() => { const t = selection(); if (t.length >= 2) setMarking(t); }}>Mark selection as personal</Button>}
          </div>
          {pdfMode ? (
            <div className={`cv-panes show-${tab}`}>
              <section aria-label="Original"><h3>Original {reveal ? "" : <small>(blurred)</small>}</h3>
                <PdfViewer docId={source.slice(9)} appId={appId} blur={!reveal} height="100%" scrollRatio={ratio?.from === "r" ? ratio.v : null} onScrollRatio={(v) => setRatio({ from: "l", v })} /></section>
              <section aria-label="Redacted"><h3>Redacted <small>(what the AI sees)</small></h3>
                <PdfViewer docId={source.slice(9)} appId={appId} blur aiInitial height="100%" scrollRatio={ratio?.from === "l" ? ratio.v : null} onScrollRatio={(v) => setRatio({ from: "r", v })} /></section>
            </div>
          ) : (
          <div className={`cv-panes show-${tab}`}>
            <section aria-label="Original"><h3>Original {data.original_shown ? "" : <small>(hidden)</small>}</h3>
              <Pane text={data.original} spans={spans} side="orig" active={active} setActive={setActive} refEl={left} onScroll={link(left, right)} shown={data.original_shown} /></section>
            <section aria-label="Redacted"><h3>Redacted <small>(what the AI sees)</small></h3>
              <Pane text={data.redacted} spans={spans} side="red" active={active} setActive={setActive} refEl={right} onScroll={link(right, left)} shown /></section>
          </div>
          )}
          {cur && (
            <div className="cv-actions" role="region" aria-label="This span">
              <span><b>{cur.type}</b> <code>{cur.token}</code></span>
              <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why is this not personal? (required)" aria-label="Why is this not personal?" />
              <Button variant="secondary" disabled={busy || !note.trim()} onClick={() => void keep()}>Keep visible (not personal)</Button>
              <small className="muted">Logged. Redaction and the leak scan run again.</small>
            </div>
          )}
        </>
      )}
      {marking && <MarkPersonal appId={appId} source={source} text={marking} onReselect={() => setMarking(null)} onClose={() => setMarking(null)}
        onDone={() => { setMarking(null); load(false); setReveal(false); onChanged(); }} />}
    </div>,
    document.body,
  );
}
