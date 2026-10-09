"""Step 3: PDF extraction, metadata stripping, manual-review flagging (synthetic data only)."""

from __future__ import annotations

import io

import pytest

pytest.importorskip("pdfplumber")
pytest.importorskip("en_core_web_lg")

from redaction.documents import DocumentRedactor, manual_review_list  # noqa: E402
from redaction.extract import extract_document, sniff_kind, strip_pdf_metadata  # noqa: E402
from redaction.structured import FieldRedactor  # noqa: E402
from tests.redaction.pdfs import PNG_1PX, make_pdf  # noqa: E402

LETTER = [
    "FICTIONAL HIGH SCHOOL HANOI - LETTERHEAD",
    "To whom it may concern,",
    "I have known Linh Tran for 3 years as her science teacher.",
    "Linh is dedicated and achieved excellent results.",
    "Referee name: Ms Hoa Pham",
    "Position: Head of Science",
    "Email: hoa.pham@example.invalid  Phone: +84 24 3800 0000",
    "Date: 12 May 2026",
]


# ---------------------------------------------------------------- extraction


def test_text_pdf_extracted_with_page_numbers():
    doc = extract_document("d1", "letter.pdf", make_pdf([LETTER, ["Second page: the applicant also volunteers weekly."]]))
    assert doc.status == "ok" and not doc.needs_manual_review and doc.include_in_ai_input
    assert [p.number for p in doc.pages] == [1, 2]
    assert "[page 1]" in doc.text and "[page 2]" in doc.text
    assert "I have known Linh Tran for 3 years" in doc.text


def test_scanned_pdf_flagged_and_excluded_no_ocr():
    doc = extract_document("d2", "scan.pdf", make_pdf([None, None]))
    assert doc.status == "no_text" and doc.needs_manual_review and not doc.include_in_ai_input
    assert doc.text == "" and "no extractable text" in doc.reason.lower()


def test_partly_scanned_pdf_keeps_text_pages_and_flags_the_rest():
    doc = extract_document("d3", "mixed.pdf", make_pdf([LETTER, None]))
    assert doc.status == "ok" and doc.include_in_ai_input and doc.needs_manual_review
    assert doc.pages_without_text == [2] and "Pages 2 of 2" in doc.reason
    assert "[page 2]" not in doc.text


@pytest.mark.parametrize("data, name", [(PNG_1PX, "headshot.png"), (b"\xff\xd8\xff\xe0" + b"0" * 20, "photo.jpg")])
def test_image_files_are_unsupported_and_need_review(data, name):
    doc = extract_document("d4", name, data)
    assert doc.status == "unsupported" and doc.needs_manual_review and not doc.include_in_ai_input
    assert "no ocr" in doc.reason.lower()


def test_unknown_and_damaged_files_need_review():
    assert extract_document("d5", "file.docx", b"PK\x03\x04 not supported").status == "unsupported"
    broken = extract_document("d6", "broken.pdf", b"%PDF-1.4\n garbage")
    assert broken.needs_manual_review and not broken.include_in_ai_input


def test_sniff_kind_uses_content_not_extension():
    assert sniff_kind(PNG_1PX, "looks_like.pdf") == "image:png"
    assert sniff_kind(make_pdf([LETTER]), "named.txt") == "pdf"


# ---------------------------------------------------------------- metadata


def test_pdf_metadata_is_never_in_text_and_is_stripped():
    # The metadata author is NOT mentioned in the visible letter text.
    data = make_pdf([LETTER], info={"Author": "Quinn Fictionalauthor", "Title": "Reference for Linh Tran",
                                    "Producer": "FakeWriter 1.0"}, xmp_author="Quinn Fictionalauthor")
    doc = extract_document("d7", "letter.pdf", data)
    assert doc.metadata_keys == ["Author", "Producer", "Title"]       # keys only
    assert "Reference for Linh Tran" not in doc.text                    # metadata never enters the text
    assert "Quinn Fictionalauthor" in doc.metadata_personal_values

    stripped = strip_pdf_metadata(data)
    assert b"Fictionalauthor" not in stripped and b"Reference for Linh Tran" not in stripped and b"/Author" not in stripped
    assert b"dc:creator" not in stripped  # XMP metadata stream removed too
    again = extract_document("d7", "letter.pdf", stripped)
    assert again.metadata_keys in ([], ["Producer"]) and not again.metadata_personal_values
    assert again.text == doc.text  # content is unchanged


def test_metadata_author_is_redacted_if_it_appears_in_text():
    import pdfplumber

    data = make_pdf([["Prepared by Q. Nakamura-Ellis for the school office."]], info={"Author": "Q. Nakamura-Ellis"})
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        assert pdf.metadata["Author"] == "Q. Nakamura-Ellis"
    doc = extract_document("d8", "note.pdf", data)
    fr = FieldRedactor()
    base = fr.redact({"fields": {"applicant_name": "Linh Tran"}, "answers": {}})
    [rd] = DocumentRedactor(fr.cfg, fr.detector).redact([doc], base.token_map, base.known_values)
    assert "Nakamura" not in rd.redacted_text


# ---------------------------------------------------------------- redaction of documents with the shared token map


def test_documents_share_tokens_with_the_form_and_referees_get_referee_tokens():
    fr = FieldRedactor()
    app = fr.redact({"fields": {"applicant_name": "Linh Tran", "email": "linh.tran@example.invalid"}, "answers": {}})
    docs = [extract_document("doc-b", "letter.pdf", make_pdf([LETTER])),
            extract_document("doc-a", "scan.pdf", make_pdf([None]))]
    out = DocumentRedactor(fr.cfg, fr.detector).redact(docs, app.token_map, app.known_values,
                                                       kinds={"doc-b": "referee_letter"})
    letter = next(d for d in out if d.document_id == "doc-b")
    assert "[PERSON_1]" in letter.redacted_text            # the applicant, same token as on the form
    assert "Linh" not in letter.redacted_text and "Tran" not in letter.redacted_text
    assert "[REFEREE_1]" in letter.redacted_text and "Hoa Pham" not in letter.redacted_text
    assert "hoa.pham@" not in letter.redacted_text and "3800 0000" not in letter.redacted_text
    for kept in ("Head of Science", "3 years", "12 May 2026", "science teacher"):
        assert kept in letter.redacted_text, kept    # referee role, association and date survive

    review = manual_review_list(out)
    assert review == [{"document_id": "doc-a", "extraction_status": "no_text",
                       "reason": review[0]["reason"], "excluded_from_ai_input": True, "pages_without_text": [1]}]
    assert "Linh" not in repr(review) and "Hoa" not in repr(review)  # reasons only, never content


def test_document_order_does_not_change_numbering():
    fr = FieldRedactor()
    letter2 = [l.replace("Hoa Pham", "Minh Le").replace("hoa.pham", "minh.le") for l in LETTER]

    def run(order):
        app = fr.redact({"fields": {"applicant_name": "Linh Tran"}, "answers": {}})
        docs = [extract_document(i, f"{i}.pdf", make_pdf([lines])) for i, lines in order]
        return {d.document_id: d.redacted_text for d in DocumentRedactor(fr.cfg, fr.detector).redact(
            docs, app.token_map, app.known_values, kinds={"a": "referee_letter", "b": "referee_letter"})}

    assert run([("a", LETTER), ("b", letter2)]) == run([("b", letter2), ("a", LETTER)])
