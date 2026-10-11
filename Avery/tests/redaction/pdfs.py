"""Build small synthetic PDFs for tests (no extra dependencies).

make_pdf([["line", ...], None, ...]) - each item is a page: a list of text
lines, or None for an image-only page (a grey image, no text: like a scan).
"""

from __future__ import annotations


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str] | None], info: dict[str, str] | None = None, xmp_author: str | None = None) -> bytes:
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    image = add(b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceGray /BitsPerComponent 8 "
                b"/Length 4 >>\nstream\n\x80\x90\xa0\xb0\nendstream")
    pages_id = len(objs) + 1 + 2 * len(pages) + (1 if xmp_author else 0) + 1  # placeholder, fixed below
    page_ids = []
    for lines in pages:
        if lines is None:
            content = b"q 500 0 0 700 50 50 cm /Im1 Do Q"
        else:
            ops = ["BT /F1 11 Tf 50 760 Td 14 TL"] + [f"({_esc(l)}) Tj T*" for l in lines] + ["ET"]
            content = "\n".join(ops).encode("latin-1")
        c = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        page_ids.append(add(b"<< /Type /Page /Parent PAGES 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                            b"/Resources << /Font << /F1 %d 0 R >> /XObject << /Im1 %d 0 R >> >> >>" % (c, font, image)))
    pages_id = add(b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % p for p in page_ids) + b"] /Count %d >>" % len(page_ids))
    objs[:] = [o.replace(b"PAGES 0 R", b"%d 0 R" % pages_id) for o in objs]
    catalog_extra = b""
    if xmp_author:
        xmp = (f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
               f'<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>{xmp_author}</dc:creator>'
               f"</rdf:Description></rdf:RDF></x:xmpmeta>").encode()
        meta_id = add(b"<< /Type /Metadata /Subtype /XML /Length %d >>\nstream\n" % len(xmp) + xmp + b"\nendstream")
        catalog_extra = b" /Metadata %d 0 R" % meta_id
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R%s >>" % (pages_id, catalog_extra))
    info_id = None
    if info:
        info_id = add(b"<< " + b" ".join(f"/{k} ({_esc(v)})".encode("latin-1") for k, v in info.items()) + b" >>")

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


PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5f0000000049454e44ae426082"
)
