"""Where every word of a text PDF sits on its page (pdfplumber), tied to the character offsets of the text redaction uses.

`extract_layout(extracted, data)` returns {pages: [{page, width, height}], words: [[page, x0, top, x1, bottom, start, end], ...]}
in PDF points, origin at the top left, with start/end offsets into `extracted.text` (the same text redaction and the AI see
before redaction). Words that cannot be matched to the text (a ligature, a hyphenation the extractor rewrote) get no offsets
and are left out: a position is never guessed.

`locate(layout, start, end)` turns a character span into one rectangle per line. `find_text` finds a passage in the text
tolerating wrapped lines, hyphenation, ligatures and smart quotes, and says how exact the match was.
"""

from __future__ import annotations

import io
import re
import unicodedata
from typing import Any

from redaction.extract import ExtractedDocument

LINE_TOLERANCE = 3.0     # points: words whose tops differ by less than this are on the same line
_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
_QUOTES = {"‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"', "–": "-", "—": "-", "‐": "-", "‑": "-", " ": " ", "­": ""}


def extract_layout(extracted: ExtractedDocument, data: bytes) -> dict[str, Any] | None:
    if extracted.file_kind != "pdf" or not extracted.pages:
        return None
    import pdfplumber

    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception:
        return None
    pages_meta: list[dict[str, Any]] = []
    words: list[list[Any]] = []
    # Offsets are in extracted.text: "[page n]\n<text>" blocks joined by "\n", in the order of extracted.pages.
    base = 0
    by_number = {p.number: p.text for p in extracted.pages}
    with pdf:
        for i, page in enumerate(pdf.pages, start=1):
            pages_meta.append({"page": i, "width": round(float(page.width), 2), "height": round(float(page.height), 2)})
            text = by_number.get(i)
            if text is None:
                continue
            marker = f"[page {i}]\n"
            cursor = 0
            try:
                page_words = page.extract_words(x_tolerance=3, y_tolerance=3, keep_blank_chars=False, use_text_flow=False)
            except Exception:
                page_words = []
            for w in page_words:
                token = w["text"]
                at = text.find(token, cursor)
                if at < 0:
                    # A wrapped hyphenated word: pdfplumber gives "wonder-" then "ful"; the text has the same pieces, so this is
                    # only reached for a rewritten ligature. Leave it out rather than guess where it is.
                    continue
                words.append([i, round(float(w["x0"]), 2), round(float(w["top"]), 2), round(float(w["x1"]), 2),
                              round(float(w["bottom"]), 2), base + len(marker) + at, base + len(marker) + at + len(token)])
                cursor = at + len(token)
            base += len(marker) + len(text) + 1   # the "\n" that joins the page blocks
    return {"pages": pages_meta, "words": words}


def locate(layout: dict[str, Any] | None, start: int, end: int) -> dict[str, Any]:
    """One rectangle per line for the characters [start, end). `approximate` is true when the span starts or ends inside a word
    (the box is cut proportionally, which is exact only for even letter widths) or part of the span has no recorded box."""
    if not layout or end <= start:
        return {"found": False, "rects": [], "approximate": False}
    hit = [w for w in layout["words"] if w[6] > start and w[5] < end]
    if not hit:
        return {"found": False, "rects": [], "approximate": False}
    approximate = False
    boxes: list[tuple[int, float, float, float, float]] = []   # page, x0, top, x1, bottom
    for page, x0, top, x1, bottom, s, e in hit:
        if s < start or e > end:   # partial word: cut the box in proportion to the characters
            approximate = True
            width, n = x1 - x0, max(1, e - s)
            lo, hi = max(start, s) - s, min(end, e) - s
            x0, x1 = x0 + width * lo / n, x0 + width * hi / n
        boxes.append((page, x0, top, x1, bottom))
    covered = sum(min(end, w[6]) - max(start, w[5]) for w in hit)
    if covered < (end - start) * 0.6:   # many characters in the span have no recorded word
        approximate = True
    boxes.sort(key=lambda b: (b[0], b[2], b[1]))
    lines: list[list[Any]] = []
    for page, x0, top, x1, bottom in boxes:
        if lines and lines[-1][0] == page and abs(lines[-1][2] - top) < LINE_TOLERANCE:
            ln = lines[-1]
            ln[1], ln[3], ln[2], ln[4] = min(ln[1], x0), max(ln[3], x1), min(ln[2], top), max(ln[4], bottom)
        else:
            lines.append([page, x0, top, x1, bottom])
    return {"found": True, "approximate": approximate,
            "rects": [{"page": p, "x0": round(a, 2), "y0": round(t, 2), "x1": round(b, 2), "y1": round(d, 2)} for p, a, t, b, d in lines]}


def _normalise(text: str) -> tuple[str, list[int]]:
    """Casefolded text with whitespace collapsed, quotes and dashes unified, ligatures expanded and "-\\n" hyphenation joined,
    plus the original index of every kept character."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "-" and i + 1 < n and text[i + 1] == "\n" and out and out[-1].isalpha():
            i += 2   # hyphenation at the end of a line: join the halves
            while i < n and text[i] in " \t":
                i += 1
            continue
        for piece in (_LIGATURES.get(ch) or _QUOTES.get(ch, ch)) or "":
            ch2 = " " if piece.isspace() else piece
            if ch2 == " ":
                if prev_space:
                    continue
                prev_space = True
            else:
                prev_space = False
            for c in unicodedata.normalize("NFKC", ch2).casefold():
                out.append(c)
                idx.append(i)
        i += 1
    return "".join(out), idx


def find_text(doc_text: str, needle: str) -> dict[str, Any]:
    """Where `needle` is in `doc_text`: {found, start, end, method: exact|normalised}. Never fuzzy: an approximate match is not a
    position, so a passage that cannot be found is reported as not found."""
    needle = needle.strip()
    if not needle or not doc_text:
        return {"found": False}
    at = doc_text.find(needle)
    if at >= 0:
        return {"found": True, "start": at, "end": at + len(needle), "method": "exact"}
    nt, idx = _normalise(doc_text)
    nn, _ = _normalise(needle)
    nn = nn.strip()
    at = nt.find(nn) if nn else -1
    if at >= 0:
        return {"found": True, "start": idx[at], "end": idx[at + len(nn) - 1] + 1, "method": "normalised"}
    return {"found": False}
