// Turning evidence (a quote, a summary passage, a pair of values) into things to highlight on a document's PDF.
// A passage that came from the application form is looked up in the PDF that holds the applicant's answers (the "application
// responses" file), by its words, because the form text and the PDF text are laid out differently.
import type { Detail } from "../../api";
import type { HighlightKind, PdfItem } from "./PdfViewer";

export interface EvidenceSpan {
  id: string; kind: HighlightKind; source: string; start?: number | null; end?: number | null; text?: string | null; label?: string;
}
export interface PdfTarget { docId: string; title: string; items: PdfItem[] }

export const FORM = "application_text";

/** The PDF document a text source maps to: its own id, or for the form the file holding the applicant's answers. */
export function docForSource(detail: Pick<Detail, "documents">, source: string): { id: string; name: string; direct: boolean } | null {
  const live = detail.documents.filter((d) => !d.superseded && d.is_pdf);
  if (source.startsWith("document:")) {
    const d = live.find((x) => `document:${x.id}` === source);
    return d ? { id: d.id, name: d.file_name, direct: true } : null;
  }
  const twin = live.find((d) => d.declared_type === "application responses");
  return twin ? { id: twin.id, name: twin.file_name, direct: false } : null;
}

/** Group evidence by the PDF it sits on. Items from a PDF's own text use exact offsets; the rest are found by their words. */
export function pdfTargets(detail: Pick<Detail, "documents">, spans: EvidenceSpan[]): PdfTarget[] {
  const out = new Map<string, PdfTarget>();
  for (const sp of spans) {
    const doc = docForSource(detail, sp.source);
    if (!doc) continue;
    const t = out.get(doc.id) ?? { docId: doc.id, title: doc.name, items: [] };
    // Offsets mean the document's own text only. For the form they refer to a different layout, so use the words.
    t.items.push(doc.direct && sp.start != null && sp.end != null
      ? { id: sp.id, kind: sp.kind, start: sp.start, end: sp.end, label: sp.label }
      : { id: sp.id, kind: sp.kind, text: sp.text, label: sp.label });
    out.set(doc.id, t);
  }
  return [...out.values()];
}

import type { Finding } from "../../api";
import { fieldLabel } from "./labels";

/** What to highlight for one eligibility finding: its verified quote(s), else the source fields it was worked out from
 *  (the typed answer in the form's PDF, and the value on the document). */
export function ruleEvidence(f: Finding, detail: Pick<Detail, "documents" | "application" | "source_texts">): EvidenceSpan[] {
  const out: EvidenceSpan[] = [];
  if (f.evidence_quote_restored && f.quote_verified) {
    const sp = f.evidence_span;
    out.push({ id: "quote", kind: "quote", source: sp?.source ?? FORM, start: sp?.start, end: sp?.end, text: f.evidence_quote_restored, label: "Verified quote" });
  }
  f.supporting_quotes_restored.filter((q) => q.verified).forEach((q, i) =>
    out.push({ id: `sq${i}`, kind: "quote", source: q.span?.source ?? q.source ?? FORM, start: q.span?.start, end: q.span?.end, text: q.quote, label: q.label ?? "Verified quote" }));
  if (out.length) return out;
  const typed = { ...(detail.application.application_text.fields ?? {}), ...(detail.application.application_text.answers ?? {}) } as Record<string, string>;
  (f.rule_sources ?? []).forEach((r, i) => {
    if (r.typed && typed[r.typed]) out.push({ id: `tf${i}`, kind: "quote", source: FORM, text: typed[r.typed], label: `Source field: ${fieldLabel(r.typed)}` });
    const doc = r.document_type ? detail.documents.find((d) => !d.superseded && (d.declared_type === r.document_type || d.detected_type === r.document_type)) : undefined;
    const value = doc && r.document_field ? doc.extracted_fields?.[r.document_field] : undefined;
    if (doc && value) out.push({ id: `df${i}`, kind: "quote", source: `document:${doc.id}`, text: value, label: `Source field on the document` });
  });
  return out;
}
