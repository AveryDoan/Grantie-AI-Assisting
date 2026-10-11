"""Step 2: structured-field redaction and the tokenizer (synthetic data only)."""

from __future__ import annotations

import copy
import re

import pytest

pytest.importorskip("presidio_analyzer")
pytest.importorskip("en_core_web_lg")

from redaction.location import classify_location  # noqa: E402
from redaction.structured import APPLICATION_SOURCE, FieldRedactor  # noqa: E402
from seed import data  # noqa: E402

SNT_CASES = [c for c in data.CASES if c["code"].startswith("N")]


@pytest.fixture(scope="module")
def redactor() -> FieldRedactor:
    return FieldRedactor()


def rebuild(result) -> str:
    """Put every occurrence's exact original back, using recorded positions."""
    text, out, pos = result.redacted_text, [], 0
    for o in sorted(result.token_map.occurrences[APPLICATION_SOURCE], key=lambda o: o.red_start):
        out.append(text[pos:o.red_start])
        out.append(o.original)
        pos = o.red_end
    return "".join(out) + text[pos:]


# ---------------------------------------------------------------- 1. known values never leak


@pytest.mark.parametrize("case", SNT_CASES, ids=lambda c: c["code"])
def test_known_structured_values_never_in_redacted_text(redactor, case):
    fields = case["application_text"]["fields"]
    r = redactor.redact(case["application_text"])
    low = r.redacted_text.casefold()
    for name in ("applicant_name", "email", "phone", "date_of_birth", "residential_address", "postal_address",
                 "declaration_name"):
        assert str(fields[name]).casefold() not in low, name
    for part in str(fields["applicant_name"]).split():
        assert not re.search(rf"\b{re.escape(part.casefold())}\b", low), part
    digits = re.sub(r"\D", "", fields["phone"])
    assert digits not in re.sub(r"\D", "", r.redacted_text)
    assert "fictional lane" not in low and "fictional street" not in low


# ---------------------------------------------------------------- 2. one person, one token


def test_same_person_same_token_across_fields_and_forms(redactor):
    app = copy.deepcopy(next(c for c in SNT_CASES if c["code"] == "N01")["application_text"])
    app["answers"]["leadership"] = "TRAN, Linh led the club. Linh organised workshops with her friend Mai Nguyen."
    r = redactor.redact(app)
    f = r.redacted_fields
    assert f["fields"]["applicant_name"] == "[PERSON_1]" and f["fields"]["declaration_name"] == "[PERSON_1]"
    assert f["answers"]["leadership"].startswith("[PERSON_1] led the club. [PERSON_1] organised")
    assert "[PERSON_2]" in f["answers"]["leadership"]  # a different person gets a different token
    assert "[PERSON_1]" in f["answers"]["biography"]


def test_email_and_phone_reuse_their_token_in_free_text(redactor):
    app = copy.deepcopy(next(c for c in SNT_CASES if c["code"] == "N01")["application_text"])
    app["answers"]["community_engagement"] = "Contact me at LINH.TRAN@example.invalid or on +84 900 000 000."
    r = redactor.redact(app)
    assert r.redacted_fields["answers"]["community_engagement"] == "Contact me at [EMAIL_1] or on [PHONE_1]."


# ---------------------------------------------------------------- 3. rule facts survive


def test_rule_relevant_facts_survive(redactor):
    app = copy.deepcopy(next(c for c in SNT_CASES if c["code"] == "N01")["application_text"])
    app["answers"]["current_study"] += " I hold a subclass 500 visa and the CDU Global Merit Scholarship."
    r = redactor.redact(app)
    t = r.redacted_text
    for kept in ("education_provider: Charles Darwin University", "course_name: Bachelor of Nursing",
                 "course_start_date: 2026-10-05", "arrival_date: 2026-09-20", "declaration_date: 2026-08-15",
                 "residential_country: Vietnam", "nationality: Vietnamese", "subclass 500", "CDU Global Merit Scholarship",
                 "study_load: Full-time", "under_18: No"):
        assert kept in t, kept
    assert r.location_class == "outside_australia"
    assert "[LOCATION: outside Australia]" in t


def test_dates_keep_their_original_format(redactor):
    app = {"fields": {"applicant_name": "Ana Silva", "date_of_birth": "04/07/2000"},
           "answers": {"plans": "I arrive on 20/09/2026 and my course starts 5 October 2026. I was born on 4 July 2000."}}
    r = redactor.redact(app)
    a = r.redacted_fields["answers"]["plans"]
    assert "20/09/2026" in a and "5 October 2026" in a  # untouched
    assert "4 July 2000" not in a and "[DOB_1]" in a     # DOB in another format is still caught
    assert r.redacted_fields["fields"]["date_of_birth"] == "[DOB_1]"


# ---------------------------------------------------------------- location handling


@pytest.mark.parametrize(
    "address, country, expected",
    [
        ("12 Fictional Lane, Hoan Kiem, Hanoi, Vietnam", "Vietnam", "outside_australia"),
        ("8 Fictional Street, Darwin NT 0800, Australia", "Australia", "nt_australia"),
        ("3 Example Road, Alice Springs", "Australia", "nt_australia"),
        ("5 Example Ave, Parramatta NSW 2150", "Australia", "australia_outside_nt"),
        ("5 Example Ave, Parramatta", "Australia", "unknown"),           # Australian, but no state: never assumed
        ("8 Fictional Street, Darwin NT 0800, Australia", "Vietnam", "unknown"),  # conflicting evidence
        ("Unit 4, 22 Example St, Kathmandu", None, "unknown"),
        ("22 Example St, Kathmandu, Nepal", None, "outside_australia"),
        (None, None, "unknown"),
    ],
)
def test_location_class(address, country, expected):
    from redaction.config import default_config

    assert classify_location(address, country, default_config().country_set()) == expected


def test_nt_resident_location_class_and_placeholder(redactor):
    r = redactor.redact(next(c for c in SNT_CASES if c["code"] == "N07")["application_text"])
    assert r.location_class == "nt_australia"
    assert r.redacted_fields["fields"]["residential_address"] == "[LOCATION: NT, Australia]"


# ---------------------------------------------------------------- only spans change; idempotent; deterministic


@pytest.mark.parametrize("code", ["N01", "N08", "N09"])
def test_only_sensitive_spans_change(redactor, code):
    """Plain and second-language English is never rewritten: putting the
    originals back gives the original text exactly."""
    r = redactor.redact(next(c for c in SNT_CASES if c["code"] == code)["application_text"])
    assert rebuild(r) == r.original_text


def test_idempotent_same_tokens_and_map(redactor):
    app = next(c for c in SNT_CASES if c["code"] == "N05")["application_text"]
    a, b = redactor.redact(app), redactor.redact(app)
    assert a.redacted_text == b.redacted_text
    assert {t: (e.token_type, e.variants) for t, e in a.token_map.entries.items()} == \
           {t: (e.token_type, e.variants) for t, e in b.token_map.entries.items()}


def test_numbering_does_not_depend_on_input_order(redactor):
    app = next(c for c in SNT_CASES if c["code"] == "N01")["application_text"]
    shuffled = {"fields": dict(reversed(list(app["fields"].items()))), "answers": dict(reversed(list(app["answers"].items())))}
    a, b = redactor.redact(app), redactor.redact(shuffled)
    assert a.redacted_fields == b.redacted_fields


def test_report_has_counts_and_no_values(redactor):
    app = next(c for c in SNT_CASES if c["code"] == "N01")["application_text"]
    r = redactor.redact(app)
    flat = repr(r.report).casefold()
    assert r.report["counts"]["PERSON"] >= 2 and r.report["location_class"] == "outside_australia"
    for v in ("linh", "tran", "example.invalid", "2007-03-12", "fictional lane"):
        assert v not in flat
