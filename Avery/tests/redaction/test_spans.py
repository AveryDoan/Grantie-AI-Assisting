"""Step 6: map quote spans from redacted text back to the original (synthetic data only)."""

from __future__ import annotations

import random

import pytest

pytest.importorskip("presidio_analyzer")
pytest.importorskip("en_core_web_lg")

from redaction.detector import Detector  # noqa: E402
from redaction.documents import DocumentRedactor, document_source  # noqa: E402
from redaction.extract import extract_document  # noqa: E402
from redaction.spans import SpanMapper, quote_to_original  # noqa: E402
from redaction.structured import APPLICATION_SOURCE, FieldRedactor  # noqa: E402
from seed import data  # noqa: E402
from tests.redaction.pdfs import make_pdf  # noqa: E402

N01 = next(c for c in data.CASES if c["code"] == "N01")["application_text"]
LETTER = ["To whom it may concern,", "I have known Linh Tran for 3 years. TRAN, Linh led our health club.",
          "Referee name: Ms Hoa Pham", "Email: hoa.pham@example.invalid", "Date: 12 May 2026"]


@pytest.fixture(scope="module")
def redacted():
    det = Detector()
    fr = FieldRedactor(det.cfg, det)
    app = fr.redact(N01)
    doc = extract_document("doc-1", "ref.pdf", make_pdf([LETTER]))
    [rd] = DocumentRedactor(det.cfg, det).redact([doc], app.token_map, app.known_values, kinds={"doc-1": "referee_letter"})
    return app, rd, doc


def restore_range(redacted_text: str, mapper: SpanMapper, rs: int, re_: int) -> str:
    """Put exact originals back into redacted_text[rs:re_] using the occurrences."""
    out, pos = [], rs
    for o in mapper.occs:
        if o.red_start >= rs and o.red_end <= re_:
            out.append(redacted_text[pos:o.red_start])
            out.append(o.original)
            pos = o.red_end
    out.append(redacted_text[pos:re_])
    return "".join(out)


# ---------------------------------------------------------------- 10. quotes map back to the original words


def test_quote_with_tokens_maps_to_original_words(redacted):
    app, _, _ = redacted
    quote = "[PERSON_1] is an aspiring nurse from [PLACE_1]"
    assert quote in app.redacted_text
    m = quote_to_original(quote, app.redacted_text, app.original_text, app.token_map, APPLICATION_SOURCE)
    assert m.original == "Linh is an aspiring nurse from Hanoi" and m.method == "exact"
    assert app.original_text[m.orig_start:m.orig_end] == m.original


def test_quote_without_tokens_maps_to_the_same_words(redacted):
    app, _, _ = redacted
    quote = "I volunteer every weekend at a community health clinic"
    m = quote_to_original(quote, app.redacted_text, app.original_text, app.token_map, APPLICATION_SOURCE)
    assert m.original == quote
    assert m.orig_start != m.red_start  # positions differ because earlier fields were redacted


def test_name_variants_in_a_document_map_exactly(redacted):
    app, rd, doc = redacted
    quote = "I have known [PERSON_1] for 3 years. [PERSON_1] led our health club."
    assert quote in rd.redacted_text
    m = quote_to_original(quote, rd.redacted_text, doc.text, app.token_map, document_source("doc-1"))
    assert m.original == "I have known Linh Tran for 3 years. TRAN, Linh led our health club."  # each form as written


def test_partial_token_is_widened_to_the_whole_token(redacted):
    app, _, _ = redacted
    text = app.redacted_text
    start = text.index("[PERSON_1] is an aspiring") + 3   # starts inside "[PERSON_1]"
    end = text.index("from [PLACE_1]") + len("from [PLA")  # ends inside "[PLACE_1]"
    rs, re_, os_, oe = SpanMapper(app.token_map, APPLICATION_SOURCE).map_span(start, end)
    assert text[rs:re_] == "[PERSON_1] is an aspiring nurse from [PLACE_1]"
    assert app.original_text[os_:oe] == "Linh is an aspiring nurse from Hanoi"


def test_whitespace_and_case_differences_still_map(redacted):
    app, _, _ = redacted
    quote = "[PERSON_1]  IS an aspiring\nnurse from [PLACE_1]"
    m = quote_to_original(quote, app.redacted_text, app.original_text, app.token_map, APPLICATION_SOURCE)
    assert m.method == "normalised" and m.original == "Linh is an aspiring nurse from Hanoi"


def test_fuzzy_quote_maps_to_the_nearest_original(redacted):
    app, _, _ = redacted
    quote = "I volunteer evry weekend at a comunity health clinic"  # two typos by the model
    m = quote_to_original(quote, app.redacted_text, app.original_text, app.token_map, APPLICATION_SOURCE)
    assert m.method == "fuzzy" and "community health clinic" in m.original


def test_quote_not_in_text_returns_none(redacted):
    app, _, _ = redacted
    assert quote_to_original("I was born in Sydney and own a farm", app.redacted_text, app.original_text,
                             app.token_map, APPLICATION_SOURCE) is None


def test_whole_text_maps_to_whole_original(redacted):
    app, _, _ = redacted
    rs, re_, os_, oe = SpanMapper(app.token_map, APPLICATION_SOURCE).map_span(0, len(app.redacted_text))
    assert (os_, oe) == (0, len(app.original_text))


# ---------------------------------------------------------------- property: random spans always map exactly


@pytest.mark.parametrize("which", ["application", "document"])
def test_random_spans_map_exactly(redacted, which):
    app, rd, doc = redacted
    if which == "application":
        red, orig, mapper = app.redacted_text, app.original_text, SpanMapper(app.token_map, APPLICATION_SOURCE)
    else:
        red, orig, mapper = rd.redacted_text, doc.text, SpanMapper(app.token_map, document_source("doc-1"))
    rng = random.Random(42)
    for _ in range(300):
        a = rng.randrange(0, len(red))
        b = rng.randrange(a, len(red) + 1)
        rs, re_, os_, oe = mapper.map_span(a, b)
        assert rs <= a and re_ >= b                       # only ever widened, never shrunk
        assert restore_range(red, mapper, rs, re_) == orig[os_:oe]
