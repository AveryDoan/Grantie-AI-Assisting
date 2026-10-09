"""Local OCR for images and scanned PDF pages (sandbox only, opt-in with --ocr).

Engine: Tesseract 5 (Apache-2.0, installed locally, e.g. `brew install tesseract`)
through pytesseract 0.3.13 (Apache-2.0). Pages are rendered with pypdfium2
(BSD-3/Apache-2.0), already a pdfplumber dependency. Nothing leaves the machine:
no cloud OCR, and the sandbox blocks the network while this runs.

OCR text is never trusted blindly:
  - per-word confidence is kept; a page whose mean confidence is low, or with
    many low-confidence words, is flagged for manual review;
  - the machine-readable zone gets a second, character-whitelisted pass
    (A-Z, 0-9, '<'), and garbled MRZ-like lines in the main text are replaced
    by the whitelisted reading when the counts agree;
  - a garbled MRZ-like line that cannot be replaced is left in place, so the
    sandbox's MRZ leak check fails and the document is blocked (fail closed)
    rather than guessed.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from redaction.extract import ExtractedDocument, PageText

DPI = 300
LOW_CONF = 60          # a word below this confidence is "low confidence"
REVIEW_MEAN = 75       # a page mean below this needs a person to read it
REVIEW_LOW_SHARE = 0.15
MRZ_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
_MRZ_CLEAN = re.compile(r"^[A-Z0-9<]{28,46}$")


@dataclass
class OcrPage:
    number: int
    mean_confidence: float
    words: int
    low_confidence_words: int


@dataclass
class OcrResult:
    document: ExtractedDocument
    engine: str
    pages: list[OcrPage] = field(default_factory=list)
    mrz_replaced: int = 0       # garbled MRZ lines replaced by the whitelisted pass
    mrz_unresolved: int = 0     # MRZ-like lines left as read (the leak check will fail)

    @property
    def needs_review(self) -> bool:
        return any(p.mean_confidence < REVIEW_MEAN or (p.words and p.low_confidence_words / p.words > REVIEW_LOW_SHARE)
                   for p in self.pages) or self.mrz_unresolved > 0

    @property
    def mean_confidence(self) -> float:
        total = sum(p.words for p in self.pages)
        return round(sum(p.mean_confidence * p.words for p in self.pages) / total, 1) if total else 0.0


def available() -> str | None:
    """The Tesseract version, or None if pytesseract or the tesseract binary is missing."""
    try:
        import pytesseract

        return str(pytesseract.get_tesseract_version())
    except Exception:
        return None


def mrz_like(line: str) -> bool:
    """A line that looks like an MRZ even if OCR garbled it ('«' for '<<', 'K' for '<', spaces)."""
    s = re.sub(r"\s+", "", line)
    if len(s) < 25:
        return False
    allowed = sum(ch.isupper() or ch.isdigit() or ch in "<«‹" for ch in s)
    fillers = s.count("<") + 2 * s.count("«") + s.count("‹")
    return allowed / len(s) >= 0.9 and (fillers >= 2 or bool(re.search(r"K{4,}", s)))


def _images(data: bytes, kind: str, pages: list[int] | None):
    from PIL import Image, ImageOps

    if kind == "pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(data)
        try:
            for i in range(len(pdf)):
                if pages is None or (i + 1) in pages:
                    yield i + 1, pdf[i].render(scale=DPI / 72).to_pil().convert("L")
        finally:
            pdf.close()
    else:
        img = Image.open(io.BytesIO(data))
        yield 1, ImageOps.exif_transpose(img).convert("L")


def _read(img) -> tuple[str, OcrPage]:
    import pytesseract
    from pytesseract import Output

    d = pytesseract.image_to_data(img, lang="eng", config="--psm 3", output_type=Output.DICT)
    lines: dict[tuple[int, int, int], list[str]] = {}
    confs: list[float] = []
    for i, word in enumerate(d["text"]):
        word = word.strip()
        conf = float(d["conf"][i])
        if not word or conf < 0:
            continue
        lines.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(word)
        confs.append(conf)
    text = "\n".join(" ".join(ws) for ws in lines.values())
    page = OcrPage(0, round(sum(confs) / len(confs), 1) if confs else 0.0, len(confs), sum(c < LOW_CONF for c in confs))
    return text, page


def _read_mrz(img) -> list[str]:
    """Whitelisted pass over the bottom of the page, where the MRZ is printed."""
    import pytesseract

    w, h = img.size
    crop = img.crop((0, int(h * 0.6), w, h))
    raw = pytesseract.image_to_string(crop, lang="eng", config=f"--psm 6 -c tessedit_char_whitelist={MRZ_WHITELIST}")
    out = []
    for line in raw.splitlines():
        s = re.sub(r"\s+", "", line)
        if _MRZ_CLEAN.match(s) and s.count("<") >= 2:
            out.append(s)
    return out


def ocr_document(document_id: str, file_name: str, data: bytes, kind: str, *,
                 base: ExtractedDocument | None = None, pages: list[int] | None = None) -> OcrResult:
    """OCR an image, or the given pages of a PDF, into an ExtractedDocument.

    base: the text-layer extraction of a PDF; its pages with text are kept as they are."""
    engine = f"tesseract {available() or '?'} (local)"
    texts: dict[int, str] = {p.number: p.text for p in (base.pages if base else [])}
    result = OcrResult(document=None, engine=engine)  # type: ignore[arg-type]
    for number, img in _images(data, kind, pages):
        text, page = _read(img)
        page.number = number
        clean = _read_mrz(img)
        lines = text.splitlines()
        garbled = [i for i, line in enumerate(lines) if mrz_like(line) and not _MRZ_CLEAN.match(re.sub(r"\s+", "", line))]
        exact = [i for i, line in enumerate(lines) if _MRZ_CLEAN.match(re.sub(r"\s+", "", line)) and "<" in line]
        candidates = sorted(garbled + exact)
        if clean and len(clean) == len(candidates):
            for i, good in zip(candidates, clean):
                if lines[i].replace(" ", "") != good:
                    result.mrz_replaced += 1
                lines[i] = good
        else:
            result.mrz_unresolved += len(garbled)
        texts[number] = "\n".join(lines)
        result.pages.append(page)
    numbers = sorted(texts)
    doc = ExtractedDocument(
        document_id=document_id, file_name=file_name, file_kind=(base.file_kind if base else kind),
        status="ok" if any(texts[n].strip() for n in numbers) else "no_text",
        needs_manual_review=False,
        reason="Text from local OCR; read it against the document.",
        pages=[PageText(n, texts[n]) for n in numbers],
        pages_without_text=[n for n in numbers if not texts[n].strip()],
        metadata_keys=list(base.metadata_keys) if base else [],
        metadata_personal_values=list(base.metadata_personal_values) if base else [],
    )
    doc.needs_manual_review = result.needs_review
    result.document = doc
    return result
