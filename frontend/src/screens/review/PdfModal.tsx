// A full-screen look at the original PDF(s) behind a piece of evidence, with highlights, and (in the redaction check) with blur.
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Button, Icon } from "../../ui";
import type { PdfTarget } from "./pdfTargets";
import { PdfViewer } from "./PdfViewer";

export function PdfModal({ appId, title, targets, blur = true, onClose, selectedId, onSelect }: {
  appId: string; title: string; targets: PdfTarget[]; blur?: boolean; onClose: () => void; selectedId?: string | null; onSelect?: (id: string) => void;
}) {
  const [tab, setTab] = useState(0);
  const first = targets[0]?.items[0];
  const [sel, setSel] = useState<string | null>(selectedId ?? (first ? (first.group ?? first.id) : null));
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopImmediatePropagation(); onClose(); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);
  const t = targets[tab];
  return createPortal(
    <div className="pm-wrap" role="dialog" aria-modal="true" aria-label={title}>
      <div className="pm-backdrop" onClick={onClose} aria-hidden="true" />
      <section className="pm">
        <header>
          <div><p className="eyebrow">Original PDF</p><h2>{title}</h2></div>
          <Button variant="secondary" icon="close" onClick={onClose}>Close</Button>
        </header>
        {targets.length === 0 && <p className="empty-state">No PDF is available for this evidence. Read the text instead.</p>}
        {targets.length > 1 && (
          <div className="rv-tabs" role="tablist">{targets.map((x, i) => <button key={x.docId} role="tab" aria-selected={i === tab} className={i === tab ? "active" : ""} onClick={() => setTab(i)}>{x.title}</button>)}</div>
        )}
        {t && <PdfViewer key={t.docId} docId={t.docId} appId={appId} items={t.items} blur={blur} selectedId={sel} onSelect={(id) => { setSel(id); onSelect?.(id); }} height={Math.max(420, window.innerHeight - 220)} />}
        <p className="muted"><Icon name="shield" size={14} />Redacted spans are blurred. Blur is a screen control for you only; anything exported is redacted on the server.</p>
      </section>
    </div>,
    document.body,
  );
}
