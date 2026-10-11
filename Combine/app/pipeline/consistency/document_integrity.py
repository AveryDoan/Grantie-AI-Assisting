"""Check 3, document_integrity (plain code, ALWAYS weak).

Metadata and layout can only ever be weak signals: "Save as PDF", scanners, re-exports and honest
corrections all change them. A weak signal is shown to the officer on the review screen and never
makes an application stand out on its own (the queue ignores applications whose only open flags are weak).
"""

from __future__ import annotations

import re
from datetime import date

from app.pipeline.consistency.common import WHEN_RE, line_matching, parse_when
from app.pipeline.consistency.context import ConsistencyContext, DocView
from app.pipeline.consistency.models import Evidence, Flag, flag

TYPE = "document_integrity"
SLACK_DAYS = 30


def _sig(doc: DocView, label: str) -> Evidence:
    return Evidence(kind="signal", source=doc.source, label=f"{label} ({doc.label})")


def _printed_date(doc: DocView) -> date | None:
    """The document's own date, as printed on it."""
    if doc.type == "referee_letter":
        line = line_matching(doc.text, r"^\s*date\s*[:\-]")
    elif doc.type == "coe":
        line = line_matching(doc.text, r"date coe issued|coe issue date")
    else:
        return None
    m = re.search(WHEN_RE, line or "", flags=re.I)
    return parse_when(m.group(0)) if m else None


def _day(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def run(ctx: ConsistencyContext) -> list[Flag]:
    out: list[Flag] = []
    for doc in ctx.docs:
        sig = doc.signals or {}
        if sig.get("kind") != "pdf" or sig.get("unreadable"):
            continue
        if sig.get("expected_fixture"):
            continue   # test data that is expected to share one producer and creation time (config/document_signals.yaml)
        created, modified, printed = _day(sig.get("created")), _day(sig.get("modified")), _printed_date(doc)
        caveat = " This is common when a document is scanned or saved again later."
        if created and printed:
            gap = (created - printed).days
            if gap > SLACK_DAYS:
                out.append(flag("document_integrity.created_after_dated", TYPE, "weak",
                                f"The PDF file was created {gap} days after the date printed on the document." + caveat,
                                [_sig(doc, f"File created {created:%d %b %Y}; document dated {printed:%d %b %Y}")], key_parts=(doc.id,)))
            elif gap < -SLACK_DAYS:
                out.append(flag("document_integrity.created_before_dated", TYPE, "weak",
                                f"The PDF file was created {-gap} days before the date printed on the document.",
                                [_sig(doc, f"File created {created:%d %b %Y}; document dated {printed:%d %b %Y}")], key_parts=(doc.id,)))
        if sig.get("software_class") == "editor":
            out.append(flag("document_integrity.editing_software", TYPE, "weak",
                            f"The PDF was made or saved with {sig.get('editor') or 'an image or PDF editing program'}, "
                            "not a word processor or scanner. Honest corrections and re-saves look the same.",
                            [_sig(doc, f"Made with {sig.get('editor') or 'an editing program'}")], key_parts=(doc.id,)))
        if created and modified and (modified - created).days > 2 and sig.get("software_class") in ("editor", "other"):
            out.append(flag("document_integrity.modified_after_created", TYPE, "weak",
                            f"The PDF was changed {(modified - created).days} days after it was created.",
                            [_sig(doc, f"Created {created:%d %b %Y}; last changed {modified:%d %b %Y}")], key_parts=(doc.id,)))
        if doc.type == "referee_letter" and sig.get("author_matches_applicant"):
            out.append(flag("document_integrity.author_is_applicant", TYPE, "weak",
                            "The PDF's author field matches the applicant's name, not a referee. "
                            "A letter prepared for a referee to sign looks the same.",
                            [_sig(doc, "The file's author field matches the applicant")], key_parts=(doc.id,)))
    if ctx.letters_need_marks:
        for doc in ctx.letters():
            ref = ctx.referee_for(doc.id)
            has_sig = bool(re.search(r"signature|signed|sincerely|faithfully|regards", doc.text, re.I)) or bool(ref and ref.stated("signature"))
            top = "\n".join([l for l in doc.text.splitlines() if l.strip() and not l.startswith("[page")][:6])
            # A letterhead shows an organisation's name (and usually its address and contacts) at the top of the letter.
            org_at_top = bool(re.search(r"\b(?:institute|university|college|school|department|ltd|pvt|pty|limited|council|hospital|"
                                        r"association|company|centre|center|office|ministry|foundation|clinic)\b", top, re.I))
            has_head = bool(re.search(r"letterhead", doc.text, re.I)) or bool(ref and ref.stated("letterhead")) or org_at_top
            for ok, cid, what in ((has_sig, "missing_signature", "signature"), (has_head, "missing_letterhead", "letterhead")):
                if not ok:
                    out.append(flag(f"document_integrity.{cid}", TYPE, "weak",
                                    f"The form asks for a {what} on each referee letter, and none shows in the text. "
                                    "A signature or letterhead may be an image: check the letter by eye.",
                                    [_sig(doc, f"No {what} found in the text")], key_parts=(doc.id, cid)))
    return out
