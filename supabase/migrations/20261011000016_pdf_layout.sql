-- =============================================================================
-- 0016 PDF layout: where every word sits on its page.
--
-- Stored at upload (and backfilled for older files) so that evidence can be highlighted on the original PDF and redacted
-- spans can be covered. pdf_layout = {pages: [{page, width, height}], words: [[page, x0, top, x1, bottom, start, end], ...]}
-- in PDF points with the origin at the top left. start and end are character offsets in documents.extracted_text, the same text
-- redaction works on, so a redacted span maps straight to boxes on the page. The word text itself is not stored here.
-- Staff only: documents are already staff-only for reading.
-- =============================================================================

alter table public.documents add column pdf_layout jsonb;
comment on column public.documents.pdf_layout is
  'Per-page sizes and word boxes (points, top-left origin) with offsets into extracted_text. Null for non-PDF files.';
