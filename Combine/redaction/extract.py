"""Step 3: document text extraction and manual-review flagging.

- Text-based PDFs: text is extracted per page with pdfplumber (MIT).
- Scanned or image-only PDFs, image files, encrypted or unreadable files:
  NO OCR is attempted. They are marked "needs manual review" and excluded
  from the AI input. A PDF with some image-only pages keeps its text pages
  and is still flagged, naming the pages an officer must read.
- PDF metadata (Author, Creator, Title...) never enters the extracted text.
  Its keys are reported (never its values), personal-looking values are
  returned as known values so they are redacted if they appear in the text,
  and strip_pdf_metadata() writes a copy without it (pypdf, BSD-3).
- pdfplumber's `repair` option is never used: it calls Ghostscript (AGPL).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Literal

ExtractionStatus = Literal["ok", "no_text", "unsupported"]

MIN_CHARS_PER_PAGE = 20  # fewer real characters than this = no usable text on the page
PERSONAL_METADATA_KEYS = ("Author", "Creator", "Title", "Subject", "Keywords", "LastModifiedBy", "Company", "Manager")

_IMAGE_SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpeg", b"GIF87a": "gif", b"GIF89a": "gif",
    b"II*\x00": "tiff", b"MM\x00*": "tiff", b"BM": "bmp",
}


@dataclass
class PageText:
    number: int  # 1-based
    text: str


@dataclass
class ExtractedDocument:
    document_id: str
    file_name: str
    file_kind: str                       # pdf | text | image:<fmt> | unknown
    status: ExtractionStatus
    needs_manual_review: bool
    reason: str | None = None            # plain words for the officer; never contains document content
    pages: list[PageText] = field(default_factory=list)
    pages_without_text: list[int] = field(default_factory=list)
    metadata_keys: list[str] = field(default_factory=list)       # e.g. ["Author", "Producer"] - keys only
    metadata_personal_values: list[str] = field(default_factory=list, repr=False)  # redaction known values; never stored or logged

    @property
    def text(self) -> str:
        """Text for redaction and the AI, with page markers. Empty if excluded."""
        return "\n".join(f"[page {p.number}]\n{p.text}" for p in self.pages if p.text.strip())

    @property
    def include_in_ai_input(self) -> bool:
        return self.status == "ok" and bool(self.text.strip())


def sniff_kind(data: bytes, file_name: str = "") -> str:
    if data[:5] == b"%PDF-":
        return "pdf"
    for sig, fmt in _IMAGE_SIGNATURES.items():
        if data.startswith(sig):
            return f"image:{fmt}"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1") or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        return "image:heic" if b"heic" in data[4:12] or b"mif1" in data[4:12] else "image:webp"
    if file_name.lower().endswith((".txt", ".md", ".csv")):
        try:
            data.decode("utf-8")
            return "text"
        except UnicodeDecodeError:
            return "unknown"
    return "unknown"


def _real_chars(text: str) -> int:
    return sum(ch.isalnum() for ch in text)


def extract_document(document_id: str, file_name: str, data: bytes, *, min_chars_per_page: int = MIN_CHARS_PER_PAGE) -> ExtractedDocument:
    kind = sniff_kind(data, file_name)
    base = dict(document_id=document_id, file_name=file_name, file_kind=kind)

    if kind.startswith("image:"):
        return ExtractedDocument(**base, status="unsupported", needs_manual_review=True,
                                 reason="Image file. Text is not read from images (no OCR); an officer must review it.")
    if kind == "text":
        text = data.decode("utf-8")
        ok = _real_chars(text) >= min_chars_per_page
        return ExtractedDocument(**base, status="ok" if ok else "no_text", needs_manual_review=not ok,
                                 reason=None if ok else "The file contains almost no text.",
                                 pages=[PageText(1, text)] if ok else [])
    if kind != "pdf":
        return ExtractedDocument(**base, status="unsupported", needs_manual_review=True,
                                 reason="Unsupported file type. Only text-based PDFs and text files are read.")

    import pdfplumber  # MIT

    try:
        pdf = pdfplumber.open(io.BytesIO(data))  # repair is deliberately off (Ghostscript is AGPL)
    except Exception as exc:  # encrypted, damaged...
        return ExtractedDocument(**base, status="unsupported", needs_manual_review=True,
                                 reason=f"The PDF could not be opened ({type(exc).__name__}).")
    with pdf:
        meta = {k: v for k, v in (pdf.metadata or {}).items() if isinstance(k, str)}
        pages: list[PageText] = []
        empty: list[int] = []
        for i, page in enumerate(pdf.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if _real_chars(text) >= min_chars_per_page:
                pages.append(PageText(i, text))
            else:
                empty.append(i)
        page_count = len(pdf.pages)

    personal = [str(meta[k]).strip() for k in PERSONAL_METADATA_KEYS
                if k in meta and isinstance(meta[k], (str, bytes)) and str(meta[k]).strip()]
    result = ExtractedDocument(**base, status="ok", needs_manual_review=False, pages=pages, pages_without_text=empty,
                               metadata_keys=sorted(meta), metadata_personal_values=personal)
    if not pages:
        result.status = "no_text"
        result.needs_manual_review = True
        result.reason = "No extractable text (probably a scan or photo). Not sent to the AI; an officer must review it."
    elif empty:
        result.needs_manual_review = True
        result.reason = (f"Pages {', '.join(map(str, empty))} of {page_count} have no extractable text "
                         "(probably scanned). Those pages were not sent to the AI; an officer must read them.")
    return result


def strip_pdf_metadata(data: bytes) -> bytes:
    """A copy of the PDF with no document metadata.

    Built from the pages only, so the information dictionary, the catalogue's
    XMP stream and any orphaned objects holding them are not carried over
    (cloning the whole file would keep them as unreferenced objects).
    Page-level XMP streams are removed too.
    """
    from pypdf import PdfReader, PdfWriter  # BSD-3
    from pypdf.generic import NameObject

    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter()
    for page in reader.pages:
        if NameObject("/Metadata") in page:
            del page[NameObject("/Metadata")]
        writer.add_page(page)
    writer.metadata = None
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
