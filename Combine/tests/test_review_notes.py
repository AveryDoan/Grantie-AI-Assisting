"""The review screen's data: why a document needs attention, the demo cases DA to DD, quote and summary verification,
and the span mapping that puts a highlight on the applicant's own words. Every case here is synthetic."""

from __future__ import annotations

import json

import pytest

from app.domain import Finding
from app.pipeline.documents import check_documents
from app.pipeline.verification import verify_findings
from tests.consistency.conftest import make_pool, make_settings, stub  # noqa: F401  (importorskips presidio / spaCy)

from app.pipeline.orchestrator import run_assessment
from app.services import queue as queue_service
from seed import data
from seed.data import sid
from seed.fraud.scenarios import FOOTER, TEST_FOOTER, build_documents

pytestmark = pytest.mark.filterwarnings("ignore")


# ------------------------------------------------------------------ why a document needs attention


def doc(i: str, declared: str, text: str) -> dict:
    return {"id": i, "declared_type": declared, "extracted_text": text}


COE = "Confirmation of Enrolment\nStudent Name: Imelda Fairweather\nCourse: Bachelor of Nursing\nCourse Start Date: 16 November 2026\nCourse End Date: 15 November 2029"
FORM = {"applicant_name": "Imelda Fairweather", "course_name": "Bachelor of Nursing", "course_start_date": "2026-11-16"}


def passport(expiry: str) -> str:
    return f"Passport\nSurname: FAIRWEATHER\nGiven names: Imelda\nNationality: Philippines\nPassport number: P4456239\nDate of expiry: {expiry}"


def reasons(typed: dict, docs: list[dict]) -> dict[str, tuple[str, str | None]]:
    out = check_documents(typed, docs)
    return {c.document_id: (c.attention_level, c.attention_reason) for c in out.checks}


def test_a_clean_set_has_no_reasons():
    r = reasons(FORM, [doc("coe", "coe", COE), doc("pp", "passport", passport("5 July 2031"))])
    assert r == {"coe": ("ok", None), "pp": ("ok", None)}


def test_passport_expiry_before_and_within_six_months_of_the_course_start():
    assert reasons(FORM, [doc("pp", "passport", passport("1 November 2026"))])["pp"] == ("attention", "Passport expires before the course starts")
    assert reasons(FORM, [doc("pp", "passport", passport("10 March 2027"))])["pp"] == ("attention", "Passport expires within 6 months of the course start")
    assert reasons(FORM, [doc("pp", "passport", passport("15 May 2027"))])["pp"][0] == "attention"      # a day short of 6 months
    assert reasons(FORM, [doc("pp", "passport", passport("16 May 2027"))])["pp"] == ("ok", None)       # exactly 6 months is enough


def test_course_start_comes_from_the_coe_when_the_form_has_none():
    typed = {k: v for k, v in FORM.items() if k != "course_start_date"}
    r = reasons(typed, [doc("coe", "coe", COE), doc("pp", "passport", passport("10 March 2027"))])
    assert r["pp"][0] == "attention"


def test_coe_name_and_date_that_differ_from_the_form():
    other = COE.replace("Imelda Fairweather", "Imelda Fairchild").replace("16 November 2026", "30 November 2026")
    level, reason = reasons(FORM, [doc("coe", "coe", other)])["coe"]
    assert level == "attention"
    assert "Name on the CoE differs from the form" in reason and "Start date on the CoE differs from the form" in reason


def test_a_document_in_the_wrong_slot_says_what_it_looks_like():
    offer = "Letter of Offer\nWe are pleased to offer you a place in the Bachelor of Nursing. Entry requirements met."
    level, reason = reasons(FORM, [doc("b", "travel booking", offer)])["b"]
    assert (level, reason) == ("attention", "This file looks like a letter of offer, not a travel booking")


def test_a_name_written_differently_is_only_a_check_and_wording_stays_neutral():
    other = COE.replace("Imelda Fairweather", "Imelda Fairweathers")
    level, reason = reasons(FORM, [doc("coe", "coe", other)])["coe"]
    assert level == "check" and "written differently" in reason
    for _lv, text in reasons(FORM, [doc("coe", "coe", COE.replace("Imelda Fairweather", "Zed Quux"))]).values():
        assert not any(w in (text or "").lower() for w in ("fraud", "fake", "suspicious", "forged"))


# ------------------------------------------------------------------ the demo cases A to D


@pytest.fixture(scope="module")
def cases():
    store, ids, actor, settings = make_pool()
    for code in ("DA", "DB", "DC", "DD"):
        run_assessment(store, actor, ids[code], stub(), settings)
    return store, ids, actor, settings


def stored(store, ids, code):
    return {d["declared_type"]: d for d in store.select("documents", eq={"application_id": ids[code]})}


def test_every_sample_document_carries_the_visible_footer():
    for sc in build_documents():
        for d in sc.docs:
            assert d.lines[-1] == TEST_FOOTER and FOOTER not in d.lines, (sc.code, d.kind)


def test_case_a_is_clean(cases):
    store, ids, *_ = cases
    assert all(d["attention_level"] == "ok" and not d["attention_reason"] for d in stored(store, ids, "DA").values())


def test_case_b_has_no_coe(cases):
    store, ids, *_ = cases
    assert "coe" not in stored(store, ids, "DB")
    assert all(d["attention_level"] == "ok" for d in stored(store, ids, "DB").values())


def test_case_c_passport_expires_soon(cases):
    store, ids, *_ = cases
    d = stored(store, ids, "DC")
    assert d["passport"]["attention_reason"] == "Passport expires within 6 months of the course start"
    assert all(v["attention_level"] == "ok" for k, v in d.items() if k != "passport")


def test_case_d_coe_differs_and_a_document_is_in_the_wrong_slot(cases):
    store, ids, *_ = cases
    d = stored(store, ids, "DD")
    assert "Name on the CoE differs from the form" in d["coe"]["attention_reason"]
    assert d["travel booking"]["type_matches"] is False
    assert d["travel booking"]["attention_reason"] == "This file looks like a letter of offer, not a travel booking"


def test_the_document_cases_are_not_linked_to_other_applications(cases):
    store, ids, actor, settings = cases
    from app.services import consistency

    groups = consistency.linked_groups(store, actor, settings)
    members = {a["id"] for g in groups for a in g["applications"]}
    assert not members & {ids[c] for c in ("DA", "DB", "DC", "DD")}


# ------------------------------------------------------------------ quote and summary verification

SOURCE = "I lead the first-aid club of nine students. Every Saturday I volunteer at a community health centre."


def finding(quotes=(), summaries=()) -> Finding:
    return Finding(rule_id="r", rule_code="M3", ai_status="Evidence only", check_source="llm", is_valid=True,
                   supporting_quotes=[{"quote": q} for q in quotes],
                   ai_summaries=[{"text": t, "passages": [{"quote": p} for p in ps]} for t, ps in summaries])


def rule():
    from app.domain import Rule

    return Rule(id="r", rule_pack_id="p", rule_code="M3", rule_text="Leadership", rule_type="judgement", check_method="llm", params={"section": "merit"})


def verified(f: Finding) -> Finding:
    return verify_findings([f], [rule()], SOURCE)[0]


def test_a_quote_that_is_in_the_text_is_verified_and_one_that_is_not_is_not():
    f = verified(finding(quotes=["I lead the first-aid club of nine students.", "I won the national prize."]))
    assert [q["verified"] for q in f.supporting_quotes] == [True, False]
    assert f.is_valid is False   # an unverified quote still makes the finding "not valid"


def test_a_summary_is_linked_only_when_code_finds_one_of_its_passages():
    f = verified(finding(summaries=[("Leads a first-aid club.", ["I lead the first-aid club of nine students."]),
                                     ("Won a prize.", ["I won the national prize."]), ("No passage.", [])]))
    assert [s["linked"] for s in f.ai_summaries] == [True, False, False]
    assert f.is_valid is True    # an unlinked summary never invalidates the finding


def test_a_summary_with_one_good_and_one_bad_passage_is_linked_and_keeps_each_verdict():
    f = verified(finding(summaries=[("Mixed.", ["Every Saturday I volunteer at a community health centre.", "made up words that are not here at all"])]))
    s = f.ai_summaries[0]
    assert s["linked"] is True and [p["verified"] for p in s["passages"]] == [True, False]


# ------------------------------------------------------------------ span mapping: the highlight lands on the applicant's own words


@pytest.fixture(scope="module")
def detail(cases):
    store, ids, actor, settings = cases
    return queue_service.application_detail(store, actor, ids["DD"], settings)


def merit(detail, code):
    return next(f for f in detail["findings"] if f["rule_code"] == code)


def test_every_located_quote_points_at_exactly_its_words_in_the_original_text(detail):
    texts = detail["source_texts"]
    located = 0
    for f in detail["findings"]:
        spans = [q["span"] for q in f["supporting_quotes_restored"] if q.get("span")] + [f["evidence_span"]] * bool(f.get("evidence_span"))
        for sp in spans:
            assert texts[sp["source"]]["text"][sp["start"]:sp["end"]] == sp["text"]
            located += 1
    assert located > 5


def test_a_summary_passage_is_located_in_the_original_text(detail):
    for code in ("M3", "M4"):
        s = merit(detail, code)["ai_summaries_restored"][0]
        assert s["linked"] and s["passages"][0]["span"]
        sp = s["passages"][0]["span"]
        assert detail["source_texts"][sp["source"]]["text"][sp["start"]:sp["end"]] == sp["text"]


def test_the_summary_has_no_score_rating_or_strength_and_no_rule_code(detail):
    blob = json.dumps(merit(detail, "M4")["ai_summaries_restored"]).lower()
    assert not any(w in blob for w in ("score", "rating", "strength", "rank", "m4"))


def test_rule_sources_name_real_field_keys_for_the_where_this_came_from_table(detail):
    s2 = next(f for f in detail["findings"] if f["rule_code"] == "S2")
    assert {"typed": "course_start_date", "document_type": "coe", "document_field": "course_start_date"} in s2["rule_sources"]
    s3 = next(f for f in detail["findings"] if f["rule_code"] == "S3")
    assert s3["rule_sources"][0]["document_field"] == "course_name"


def test_without_a_token_map_nothing_is_located_and_nothing_is_guessed(cases):
    store, ids, actor, settings = cases
    from app.services.redaction_service import QuoteRestorer

    app = store.select("applications", eq={"id": ids["DD"]})[0]
    r = QuoteRestorer(store, app, settings)
    r._tokens = False
    assert r.locate("I lead the first-aid club of twelve students", "application_text") is None
    assert r.source_texts() == {}
