"""The original PDF for officers: a short-lived signed link, evidence highlights and redaction blur, and real (burned-in) redaction
for anything that leaves the screen.

- The viewer shows the ORIGINAL file, so it is a display control only. The officer role is checked when the link is issued; the
  link then works for a few minutes and no longer (it carries its own signature and expiry).
- Highlights come from exact character spans (or, for a passage in another text, an exact or normalised text match). A passage that
  cannot be found is reported as not found. A box cut inside a word, or a normalised match, is reported as approximate.
- Blur boxes come from the redaction token map: every redacted span of the document, mapped to its place on the page.
- Exports (a redacted copy of a document, the evidence pack) are made by rendering each page to an image on the server and burning
  opaque black boxes into it. They contain no text layer and no overlay that could be lifted off.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import re
import time
from typing import Any

from app.config import Settings
from app.services.access import Actor, application_for_staff, require_role
from app.services.audit import write_audit
from app.services.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.services.redaction_service import _cipher, document_bytes
from app.store.base import Store, one

LINK_SECONDS = 300
RENDER_DPI = 150
PAD = 1.5   # points of margin around a burned box


def _secret(settings: Settings) -> bytes:
    return settings.supabase_jwt_secret.get_secret_value().encode() or b"dev"


def sign(settings: Settings, document_id: str, ttl: int = LINK_SECONDS) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"d": document_id, "e": int(time.time()) + ttl}).encode()).decode().rstrip("=")
    sig = hmac.new(_secret(settings), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def verify(settings: Settings, document_id: str, token: str) -> bool:
    try:
        body, sig = token.split(".", 1)
        good = hmac.new(_secret(settings), body.encode(), hashlib.sha256).hexdigest()[:32]
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:
        return False
    return hmac.compare_digest(sig, good) and data.get("d") == document_id and data.get("e", 0) >= time.time()


def _document(store: Store, actor: Actor, document_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    require_role(actor, "officer", "admin")
    row = one(store.select("documents", eq={"id": document_id}, limit=1))
    if not row:
        raise NotFound("Document not found")
    app = application_for_staff(store, actor, row["application_id"])   # same access rule as everything else
    return row, app


def layout_of(store: Store, row: dict[str, Any]) -> dict[str, Any] | None:
    """The stored word boxes, computed (and stored) now for a file uploaded before they were kept."""
    if row.get("pdf_layout") is not None:
        return row["pdf_layout"]
    from redaction.extract import extract_document
    from redaction.layout import extract_layout

    data, name = document_bytes(store, row)
    if data[:5] != b"%PDF-":
        return None
    ext = extract_document(row["id"], name, data)
    layout = extract_layout(ext, data)
    if layout is not None:
        store.update("documents", {"pdf_layout": layout}, eq={"id": row["id"]})
    return layout


def backfill(store: Store) -> int:
    """Word boxes for every stored PDF that has none. Returns how many were filled."""
    n = 0
    for row in store.select("documents"):
        if row.get("pdf_layout") is None and layout_of(store, row) is not None:
            n += 1
    return n


def viewer(store: Store, actor: Actor, document_id: str, settings: Settings) -> dict[str, Any]:
    row, app = _document(store, actor, document_id)
    layout = layout_of(store, row)
    if layout is None:
        raise Conflict("This file is not a PDF with a text layer, so it has no page view")
    write_audit(store, actor, "document.viewed", application_id=app["id"], details={"document_id": document_id})
    return {"document_id": document_id, "file_name": row["file_name"], "declared_type": row.get("declared_type"),
            "pages": layout["pages"], "url": f"/documents/{document_id}/file?token={sign(settings, document_id)}",
            "expires_in": LINK_SECONDS}


def file_bytes(store: Store, settings: Settings, document_id: str, token: str) -> tuple[bytes, str]:
    if not verify(settings, document_id, token):
        raise Forbidden("This link has expired or is not valid. Open the document again.")
    row = one(store.select("documents", eq={"id": document_id}, limit=1))
    if not row:
        raise NotFound("Document not found")
    data, name = document_bytes(store, row)
    return data, name


def locate_items(store: Store, actor: Actor, document_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    from redaction.layout import find_text, locate

    row, _app = _document(store, actor, document_id)
    layout = layout_of(store, row)
    text = row.get("extracted_text") or ""
    out: dict[str, Any] = {}
    for it in items[:200]:
        method = "span"
        start, end = it.get("start"), it.get("end")
        if start is None or end is None:
            hit = find_text(text, str(it.get("text") or ""))
            if not hit["found"]:
                out[it["id"]] = {"status": "not_found", "rects": [], "method": "none"}
                continue
            start, end, method = hit["start"], hit["end"], hit["method"]
        loc = locate(layout, int(start), int(end))
        if method == "normalised" and re.sub(r"\s+", " ", text[int(start):int(end)]).strip() == re.sub(r"\s+", " ", str(it.get("text") or "")).strip():
            method = "whitespace"   # only line breaks and spacing differ: the position is as exact as an exact match
        status = "not_found" if not loc["found"] else "approximate" if (loc["approximate"] or method == "normalised") else "exact"
        out[it["id"]] = {"status": status, "rects": loc["rects"], "method": method}
    return out


def blur_boxes(store: Store, actor: Actor, document_id: str, settings: Settings) -> dict[str, Any]:
    """Every redacted span of this document, with where it sits on the page. Counted the same way as the redaction check."""
    from redaction.layout import locate
    from redaction.storage import load_token_map

    from app.services.redaction_check import CATEGORY

    row, app = _document(store, actor, document_id)
    layout = layout_of(store, row)
    if not row.get("redacted_text"):
        return {"boxes": [], "counts": {}, "redacted": False}
    tokens = load_token_map(store, app["id"], _cipher(settings))
    source = f"document:{document_id}"
    boxes = []
    counts: dict[str, int] = {}
    for o in tokens.occurrences.get(source, []):
        entry = tokens.entries[o.token]
        kind = "Location" if o.token.startswith("[LOCATION") else CATEGORY.get(entry.token_type, "Other")
        loc = locate(layout, o.orig_start, o.orig_end)
        counts[kind] = counts.get(kind, 0) + 1
        boxes.append({"id": f"{source}|{o.red_start}", "token": o.token, "type": kind, "rects": loc["rects"], "found": loc["found"]})
    return {"boxes": boxes, "counts": counts, "redacted": True}


# ---------------------------------------------------------------- real redaction: burned into an image of each page


def _render_pages(data: bytes, boxes_by_page: dict[int, list[dict[str, float]]]) -> list[Any]:
    import pdfplumber
    from PIL import ImageDraw

    pages = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            img = page.to_image(resolution=RENDER_DPI).original.convert("RGB")
            scale = RENDER_DPI / 72.0
            draw = ImageDraw.Draw(img)
            for r in boxes_by_page.get(i, []):
                draw.rectangle([(r["x0"] - PAD) * scale, (r["y0"] - PAD) * scale, (r["x1"] + PAD) * scale, (r["y1"] + PAD) * scale], fill=(0, 0, 0))
            pages.append(img)
    return pages


def _text_page(title: str, lines: list[str]) -> Any:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    y = 80
    draw.text((80, y), title, fill="black", font=font)
    y += 40
    for line in lines:
        draw.text((80, y), line[:150], fill="black", font=font)
        y += 18
        if y > 1680:
            break
    return img


def _to_pdf(images: list[Any]) -> bytes:
    out = io.BytesIO()
    images[0].save(out, "PDF", resolution=RENDER_DPI, save_all=True, append_images=images[1:])
    return out.getvalue()


def _boxes_for(store: Store, actor: Actor, row: dict[str, Any], settings: Settings) -> dict[int, list[dict[str, float]]]:
    by_page: dict[int, list[dict[str, float]]] = {}
    for b in blur_boxes(store, actor, row["id"], settings)["boxes"]:
        for r in b["rects"]:
            by_page.setdefault(r["page"], []).append(r)
    return by_page


def burned_document(store: Store, actor: Actor, document_id: str, settings: Settings) -> bytes:
    """A copy of the document that is safe to hand on: each page is an image with opaque black boxes where the AI saw a placeholder.

    A document the AI never read (no redacted text) is refused: it cannot be shared safely."""
    row, app = _document(store, actor, document_id)
    if not row.get("redacted_text"):
        raise Conflict("This document has not been redacted, so it cannot be exported")
    data, _ = document_bytes(store, row)
    if data[:5] != b"%PDF-":
        pages = [_text_page(row["file_name"], row["redacted_text"].splitlines())]
    else:
        pages = _render_pages(data, _boxes_for(store, actor, row, settings))
    write_audit(store, actor, "document.exported_redacted", application_id=app["id"], details={"document_id": document_id, "pages": len(pages)})
    return _to_pdf(pages)


def evidence_pack(store: Store, actor: Actor, application_id: str, settings: Settings) -> bytes:
    """Every redacted document of one application in one PDF, with burned-in redaction and a cover page."""
    require_role(actor, "officer", "admin")
    app = application_for_staff(store, actor, application_id)
    rows = [d for d in sorted(store.select("documents", eq={"application_id": application_id}), key=lambda d: d["id"])
            if d.get("redacted_text") and not d.get("superseded")]
    if not rows:
        raise Conflict("Nothing has been redacted yet, so there is no evidence pack")
    pages = [_text_page("Evidence pack", [
        f"Application {application_id[:8]}",
        "Personal details are covered by opaque black boxes. Each page is an image: there is no text layer and no overlay.",
        "SAMPLE DOCUMENTS - synthetic test data only.", "", "Contents:", *[f"  {i}. {d['file_name']}" for i, d in enumerate(rows, 1)]])]
    for row in rows:
        data, _ = document_bytes(store, row)
        if data[:5] == b"%PDF-":
            pages += _render_pages(data, _boxes_for(store, actor, row, settings))
        else:
            pages.append(_text_page(row["file_name"], row["redacted_text"].splitlines()))
    write_audit(store, actor, "evidence_pack.exported", application_id=application_id, details={"documents": len(rows), "pages": len(pages)})
    return _to_pdf(pages)
