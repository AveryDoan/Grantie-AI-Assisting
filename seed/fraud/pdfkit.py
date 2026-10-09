"""A tiny deterministic PDF writer for the synthetic consistency cases (no extra dependencies).

Text pages in the standard Helvetica font (a real text layer), with the document information dictionary set
EXACTLY as the scenario says. Nothing here reads the clock, so the same scenario always gives the same bytes.
"""

from __future__ import annotations

import textwrap

LINE_WIDTH = 92
LINES_PER_PAGE = 48


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf_date(iso: str, time: str = "090000") -> str:
    """'2026-10-02' -> 'D:20261002090000Z'"""
    return f"D:{iso.replace('-', '')}{time}Z"


def wrap(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        out.extend(textwrap.wrap(line, LINE_WIDTH, break_long_words=False) or [""])
    return out


def make_pdf(lines: list[str], *, info: dict[str, str] | None = None) -> bytes:
    """Wrap and paginate `lines`; `info` becomes the PDF information dictionary (Producer, Creator, CreationDate, ...)."""
    wrapped = wrap(lines)
    pages = [wrapped[i:i + LINES_PER_PAGE] for i in range(0, len(wrapped), LINES_PER_PAGE)] or [[""]]
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    page_ids = []
    for page in pages:
        ops = ["BT /F1 11 Tf 50 760 Td 14 TL"] + [f"({_esc(l)}) Tj T*" for l in page] + ["ET"]
        content = "\n".join(ops).encode("latin-1", "replace")
        c = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        page_ids.append(add(b"<< /Type /Page /Parent PAGES 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                            b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (c, font)))
    pages_id = add(b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % p for p in page_ids) + b"] /Count %d >>" % len(page_ids))
    objs[:] = [o.replace(b"PAGES 0 R", b"%d 0 R" % pages_id) for o in objs]
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)
    info_id = add(b"<< " + b" ".join(f"/{k} ({_esc(v)})".encode("latin-1") for k, v in info.items()) + b" >>") if info else None

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    trailer = b"<< /Size %d /Root %d 0 R" % (len(objs) + 1, catalog) + (b" /Info %d 0 R" % info_id if info_id else b"") + b" >>"
    out += b"trailer\n" + trailer + b"\nstartxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)
