"""Signals from a PDF's metadata, read BEFORE intake strips it.

Only DERIVED signals are returned and stored: two dates, a software class from
a closed list, and booleans. The raw author, creator and producer strings are
never stored, logged or shown (an author field is often a person's name).

These are weak signals. Scanners, "save as PDF", re-exports and legitimate edits
all change metadata; a signal here is a reason to look, never evidence of wrongdoing.
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from typing import Any

# Closed lists: the label is stored, never the raw string.
EDITORS = {
    "photoshop": "Adobe Photoshop", "illustrator": "Adobe Illustrator", "gimp": "GIMP", "inkscape": "Inkscape",
    "canva": "Canva", "ilovepdf": "iLovePDF", "smallpdf": "Smallpdf", "sejda": "Sejda", "pdfescape": "PDFescape",
    "pdf-xchange editor": "PDF-XChange Editor", "foxit phantom": "Foxit PhantomPDF", "nitro": "Nitro PDF",
    "pdffiller": "pdfFiller", "lightpdf": "LightPDF", "pdf24": "PDF24 Creator", "pdfelement": "PDFelement",
    "affinity": "Affinity", "paint.net": "Paint.NET", "pixlr": "Pixlr",
}
SCANNERS = ("scan", "epson", "canon", "xerox", "ricoh", "brother", "fujitsu", "kyocera", "konica", "camscanner",
            "genius scan", "adobe scan", "scansnap", "tiny scanner")
OFFICE = ("microsoft", "word", "libreoffice", "openoffice", "powerpoint", "pages", "writer", "google docs", "wps")
BROWSER = ("skia", "chrome", "chromium", "firefox", "headless", "wkhtml", "weasyprint")
SYSTEM = ("quartz", "preview", "print to pdf", "cups", "ghostscript")


def classify_software(*strings: str | None) -> tuple[str, str | None]:
    """(class, editor label). The class is one of editor, scanner, office, browser, system, other, unknown."""
    blob = " ".join(s for s in strings if s).lower()
    if not blob.strip():
        return "unknown", None
    for key, label in EDITORS.items():
        if key in blob:
            return "editor", label
    for words, cls in ((SCANNERS, "scanner"), (OFFICE, "office"), (BROWSER, "browser"), (SYSTEM, "system")):
        if any(w in blob for w in words):
            return cls, None
    return "other", None


def expected_fixture(producer: str | None, creator: str | None) -> str | None:
    """The name of a synthetic fixture that is EXPECTED to share this producer (config/document_signals.yaml), else None."""
    import yaml
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "config" / "document_signals.yaml"
    try:
        entries = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("expected_shared_producer") or []
    except (OSError, yaml.YAMLError):
        return None
    blob = f"{producer or ''} {creator or ''}".lower()
    for e in entries:
        needle = str(e.get("producer_contains") or "").lower()
        if needle and needle in blob:
            return str(e.get("fixture"))
    return None


def _iso(d: Any) -> str | None:
    if not isinstance(d, datetime):
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).date().isoformat()


def _tokens(name: str) -> set[str]:
    return {t for t in re.sub(r"[^a-z ]", " ", name.lower()).split() if len(t) > 2}


def read_integrity_signals(data: bytes, *, text: str = "", applicant_name: str | None = None) -> dict[str, Any]:
    """Derived signals for one uploaded file. Never raises: an unreadable file just has no signals."""
    if data[:5] != b"%PDF-":
        return {"kind": "not_pdf"}
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        meta = reader.metadata
        producer = getattr(meta, "producer", None) if meta else None
        creator = getattr(meta, "creator", None) if meta else None
        author = getattr(meta, "author", None) if meta else None
        created = _iso(getattr(meta, "creation_date", None)) if meta else None
        modified = _iso(getattr(meta, "modification_date", None)) if meta else None
        try:
            xmp = reader.xmp_metadata
        except Exception:
            xmp = None
        if xmp is not None:
            created = created or _iso(getattr(xmp, "xmp_create_date", None))
            modified = modified or _iso(getattr(xmp, "xmp_modify_date", None))
            creator = creator or getattr(xmp, "xmp_creator_tool", None)
            author = author or ((getattr(xmp, "dc_creator", None) or [None])[0])
        cls, editor = classify_software(producer, creator)
        author_tokens = _tokens(str(author)) if author else set()
        applicant_tokens = _tokens(applicant_name or "")
        head = set(re.sub(r"[^a-z ]", " ", text[:3000].lower()).split())
        return {
            "kind": "pdf",
            "pages": len(reader.pages),
            "created": created,
            "modified": modified,
            "software_class": cls,
            "editor": editor,
            "author_present": bool(author_tokens),
            "author_matches_applicant": bool(author_tokens and applicant_tokens and len(author_tokens & applicant_tokens) >= min(2, len(applicant_tokens))),
            "author_in_text": (bool(author_tokens & head) if author_tokens and text else None),
            "has_text": bool(text.strip()),
            # A synthetic fixture that is expected to share one producer and one creation time (derived name only).
            "expected_fixture": expected_fixture(producer, creator),
        }
    except Exception:
        return {"kind": "pdf", "unreadable": True}
