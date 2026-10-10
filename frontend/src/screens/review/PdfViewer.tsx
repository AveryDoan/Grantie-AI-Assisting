// The original PDF for an officer, drawn with PDF.js onto canvases, with:
//   - highlight layers: yellow = verified quote, blue = AI summary source, orange and teal = the two values of a consistency pair;
//   - blur boxes over every redacted span (the officer's default view). A box carries its token label on hover. "Show original"
//     reveals ONE item after the officer confirms, and each reveal is written to the audit log;
//   - a page-level toggle "Show what the AI saw" that turns every blur into the placeholder the AI read;
//   - no PDF.js text layer at all, so nothing in the viewer can be selected, copied or searched (the blur is a display control for
//     the officer only: anything exported is rendered and redacted on the server instead).
// Highlights and blur boxes are placed from the page's own coordinates, so they stay aligned under zoom and rotation. Where a
// highlight overlaps a blurred span the blur is on top and the highlight keeps a visible outline around it.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type PdfBlur, type PdfLocateItem, type PdfLocation, type PdfRect, type PdfViewerInfo } from "../../api";
import { Button, ErrorNotice, Icon, Loading } from "../../ui";

export type HighlightKind = "quote" | "summary" | "left" | "right";
export interface PdfItem extends PdfLocateItem { kind: HighlightKind; label?: string; group?: string }   // group: the card this highlight belongs to

const KIND_LABEL: Record<HighlightKind, string> = { quote: "Verified quote", summary: "AI summary source", left: "First value", right: "Second value" };
const STATUS_WORDS: Record<string, string> = { approximate: "Approximate position", not_found: "Could not locate on the page" };

/** A rectangle in page points (origin top left) as pixels on the displayed, scaled and rotated page. */
export function toView(r: PdfRect, W: number, H: number, scale: number, rotation: number) {
  const pts: [number, number][] = [[r.x0, r.y0], [r.x1, r.y1]];
  const map = ([x, y]: [number, number]): [number, number] =>
    rotation === 90 ? [H - y, x] : rotation === 180 ? [W - x, H - y] : rotation === 270 ? [y, W - x] : [x, y];
  const [a, b] = pts.map(map);
  return { left: Math.min(a[0], b[0]) * scale, top: Math.min(a[1], b[1]) * scale, width: Math.abs(b[0] - a[0]) * scale, height: Math.abs(b[1] - a[1]) * scale };
}

export function PdfViewer({ docId, appId, items = [], selectedId, onSelect, blur = true, height = 640 }: {
  docId: string; appId: string; items?: PdfItem[]; selectedId?: string | null; onSelect?: (id: string) => void; blur?: boolean; height?: number;
}) {
  const [info, setInfo] = useState<PdfViewerInfo | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [pdf, setPdf] = useState<{ getPage: (n: number) => Promise<any> } | null>(null);
  const [located, setLocated] = useState<Record<string, PdfLocation>>({});
  const [blurs, setBlurs] = useState<PdfBlur | null>(null);
  const [zoom, setZoom] = useState(1.1);
  const [rotation, setRotation] = useState(0);
  const [aiView, setAiView] = useState(false);
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [ask, setAsk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const itemsKey = useMemo(() => JSON.stringify(items.map((i) => [i.id, i.start, i.end, i.text, i.kind])), [items]);

  // The signed link, the document, the located highlights and the blur boxes.
  useEffect(() => {
    let live = true;
    setInfo(null); setPdf(null); setError(null);
    (async () => {
      try {
        const v = await api.documentViewer(docId);
        const lib = await import("pdfjs-dist");
        const worker = (await import("pdfjs-dist/build/pdf.worker.min.mjs?url")).default;
        lib.GlobalWorkerOptions.workerSrc = worker;
        const doc = await lib.getDocument({ url: `/api${v.url}`, disableAutoFetch: false }).promise;
        if (live) {
          setInfo(v); setPdf(doc as any);
          const w = scroller.current?.clientWidth;   // fit the page to the width available
          if (w && v.pages[0]) setZoom(Math.max(0.6, Math.min(1.1, +((w - 34) / (v.pages[0].width * 1.35)).toFixed(2))));
        }
      } catch (e) { if (live) setError(e); }
    })();
    return () => { live = false; };
  }, [docId]);
  useEffect(() => {
    let live = true;
    if (items.length) api.locateItems(docId, items.map(({ id, start, end, text }) => ({ id, start, end, text }))).then((r) => live && setLocated(r)).catch(() => undefined);
    else setLocated({});
    return () => { live = false; };
  }, [docId, itemsKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    let live = true;
    if (blur) api.documentBlur(docId).then((b) => live && setBlurs(b)).catch(() => undefined);
    return () => { live = false; };
  }, [docId, blur]);

  // Bring the selected highlight into view (its page first, then the box).
  useEffect(() => {
    if (!selectedId) return;
    const t = setTimeout(() => scroller.current?.querySelector(`[data-hlg="${CSS.escape(selectedId)}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" }), 150);
    return () => clearTimeout(t);
  }, [selectedId, located, zoom, rotation, pdf]);

  const reveal = useCallback(async (boxId: string) => {
    setBusy(true);
    try { setRevealed((r) => ({ ...r, [boxId]: "" })); const out = await api.revealItem(appId, boxId); setRevealed((r) => ({ ...r, [boxId]: out.original })); }
    catch (e) { setError(e); setRevealed((r) => { const { [boxId]: _x, ...rest } = r; return rest; }); }
    setBusy(false); setAsk(null);
  }, [appId]);

  if (error) return <ErrorNotice error={error} />;
  if (!info || !pdf) return <Loading label="Opening the PDF…" />;

  const total = blurs ? Object.values(blurs.counts).reduce((a, b) => a + b, 0) : 0;
  const flagged = items.filter((i) => located[i.id] && located[i.id].status !== "exact");
  const kinds = [...new Set(items.map((i) => i.kind))];

  return (
    <div className="pv" style={{ height }}>
      <div className="pv-bar">
        <Button variant="secondary" onClick={() => setZoom((z) => Math.max(0.6, +(z - 0.15).toFixed(2)))} title="Zoom out">−</Button>
        <span aria-live="polite">{Math.round(zoom * 100)}%</span>
        <Button variant="secondary" onClick={() => setZoom((z) => Math.min(2.5, +(z + 0.15).toFixed(2)))} title="Zoom in">+</Button>
        <Button variant="secondary" onClick={() => setRotation((r) => (r + 90) % 360)} title="Rotate">Rotate</Button>
        {blur && blurs?.redacted && (
          <label className="pv-toggle"><input type="checkbox" checked={aiView} onChange={(e) => setAiView(e.target.checked)} />Show what the AI saw</label>
        )}
        {blur && blurs?.redacted && <span className="pv-count">{total} redacted {total === 1 ? "span" : "spans"} on this document</span>}
      </div>
      <div className="pv-legend" aria-label="Legend">
        {kinds.map((k) => <span key={k}><i className={`pv-key ${k}`} />{KIND_LABEL[k]}</span>)}
        {blur && blurs?.redacted && <span><i className="pv-key blur" />Redacted (blurred)</span>}
        {flagged.length > 0 && <span className="pv-warn"><Icon name="question" size={13} />{flagged.map((i) => `${i.label ?? KIND_LABEL[i.kind]}: ${STATUS_WORDS[located[i.id].status]}`).join(" · ")}</span>}
      </div>
      <div className="pv-scroll" ref={scroller}>
        {info.pages.map((pg) => (
          <PdfPage key={`${pg.page}-${zoom}-${rotation}`} pdf={pdf} meta={pg} zoom={zoom} rotation={rotation}>
            {(scale) => (
              <>
                {items.map((it) => (located[it.id]?.rects ?? []).filter((r) => r.page === pg.page).map((r, i) => {
                  const box = toView(r, pg.width, pg.height, scale, rotation);
                  const loc = located[it.id];
                  return (
                    <button key={`${it.id}-${i}`} data-hl={it.id} data-hlg={it.group ?? it.id} className={`pv-hl ${it.kind} ${loc.status !== "exact" ? "approx" : ""} ${selectedId === (it.group ?? it.id) ? "sel" : ""}`}
                      style={box} onClick={() => onSelect?.(it.group ?? it.id)} aria-label={`${it.label ?? KIND_LABEL[it.kind]}${loc.status !== "exact" ? `, ${STATUS_WORDS[loc.status]}` : ""}`}
                      title={loc.status !== "exact" ? STATUS_WORDS[loc.status] : KIND_LABEL[it.kind]} />
                  );
                }))}
                {blur && (blurs?.boxes ?? []).map((b) => b.rects.filter((r) => r.page === pg.page).map((r, i) => {
                  const box = toView(r, pg.width, pg.height, scale, rotation);
                  const open = revealed[b.id] !== undefined;
                  if (open) return <div key={`${b.id}-${i}`} className="pv-revealed" style={box} title="Original shown for this item only" />;
                  return (
                    <button key={`${b.id}-${i}`} className={`pv-blur ${aiView ? "ai" : ""}`} style={box} title={b.token} aria-label={`Redacted ${b.type}: ${b.token}`}
                      onClick={() => setAsk(ask === b.id ? null : b.id)}>{aiView ? <span>{b.token}</span> : null}</button>
                  );
                }))}
              </>
            )}
          </PdfPage>
        ))}
      </div>
      {ask && (
        <div className="pv-ask" role="alertdialog" aria-label="Show original">
          <p><strong>Show the original for this item?</strong> {blurs?.boxes.find((b) => b.id === ask)?.token} ({blurs?.boxes.find((b) => b.id === ask)?.type}). The reveal is recorded in the audit log.</p>
          <div className="rule-actions"><Button disabled={busy} onClick={() => void reveal(ask)}>Yes, show original</Button><Button variant="quiet" onClick={() => setAsk(null)}>Cancel</Button></div>
        </div>
      )}
      {Object.entries(revealed).filter(([, v]) => v).map(([id, v]) => (
        <p key={id} className="pv-orig" aria-live="polite">Original (this item only): <strong>{v}</strong>
          <button className="link-button" onClick={() => setRevealed((r) => { const { [id]: _x, ...rest } = r; return rest; })}>Blur again</button></p>
      ))}
    </div>
  );
}

/** One page: a canvas drawn at the current zoom and rotation, and the overlay layer drawn over it. */
function PdfPage({ pdf, meta, zoom, rotation, children }: {
  pdf: { getPage: (n: number) => Promise<any> }; meta: { page: number; width: number; height: number }; zoom: number; rotation: number; children: (scale: number) => React.ReactNode;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const scale = zoom * 1.35;      // CSS pixels per PDF point
  const rotated = rotation === 90 || rotation === 270;
  const w = (rotated ? meta.height : meta.width) * scale;
  const h = (rotated ? meta.width : meta.height) * scale;
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const page = await pdf.getPage(meta.page);
      const dpr = window.devicePixelRatio || 1;
      const vp = page.getViewport({ scale: scale * dpr, rotation: (page.rotate + rotation) % 360 });
      const c = canvas.current;
      if (!c || cancelled) return;
      c.width = vp.width; c.height = vp.height;
      const task = page.render({ canvasContext: c.getContext("2d")!, viewport: vp, canvas: c });
      await task.promise.catch(() => undefined);
    })();
    return () => { cancelled = true; };
  }, [pdf, meta.page, scale, rotation]);
  return (
    <div className="pv-page" style={{ width: w, height: h }} data-page={meta.page}>
      <canvas ref={canvas} style={{ width: w, height: h }} aria-label={`Page ${meta.page}`} />
      <div className="pv-layer">{children(scale)}</div>
      <span className="pv-pageno">Page {meta.page}</span>
    </div>
  );
}
