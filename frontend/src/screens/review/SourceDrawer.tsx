// Side drawer: the part of the form or the document a result came from, with the exact words highlighted.
// Only the section around the field is shown for the form (not the whole application); a document is shown whole.
// With no matching text it says so and lists what was searched.
import { useEffect, useMemo, useRef } from "react";
import { createPortal } from "react-dom";
import type { Detail } from "../../api";
import { Icon } from "../../ui";
import { searchedSections, sectionAround, toBlocks, type Target } from "./highlight";
import { TextView } from "./TextView";

export function SourceDrawer({ title, detail, target, onBack }: {
  title: string; detail: Pick<Detail, "documents" | "source_texts">; target: Pick<Target, "source" | "start" | "end"> | null; onBack: () => void;
}) {
  const text = target ? detail.source_texts?.[target.source] : null;
  const isForm = target?.source === "application_text";
  const blocks = useMemo(() => (target && text && isForm ? sectionAround(toBlocks(text.text), target.start, target.end, 2) : undefined), [target, text, isForm]);
  const back = useRef<HTMLButtonElement>(null);
  useEffect(() => { back.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopImmediatePropagation(); e.stopPropagation(); onBack(); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onBack]);
  return createPortal(
    <div className="rv-drawer-wrap">
      <div className="rv-backdrop" onClick={onBack} aria-hidden="true" />
      <aside className="rv-drawer" role="dialog" aria-modal="true" aria-label="Source text">
        <header>
          <button ref={back} className="link-button" onClick={onBack}><Icon name="left" size={16} />Back to finding</button>
          <p className="eyebrow">{target && text ? text.label : "Source text"}</p>
          <h2>{title}</h2>
        </header>
        <div className="rv-drawer-body">
          {target && text ? (
            <>
              <p className="muted">{isForm ? "The part of the form around this answer. The highlighted words are the source." : "The document. The highlighted words are the source."}</p>
              <TextView text={text.text} only={blocks} highlights={[{ id: "focus", start: target.start, end: target.end, kind: "focus" }]} focusId="focus" />
            </>
          ) : (
            <div className="rv-nomatch">
              <h3>No matching text found</h3>
              <p>Nothing in the application covers this. These were searched:</p>
              <ul className="cx-list">{searchedSections(detail).map((l) => <li key={l}>{l}</li>)}</ul>
            </div>
          )}
        </div>
      </aside>
    </div>,
    document.body,
  );
}
