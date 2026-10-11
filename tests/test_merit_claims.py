"""Merit summaries: extract first, then summarise from verified claims. Unit tests for the code stages (1, 3, 4 checks, 5)."""

from __future__ import annotations

import pytest

from app.llm.base import LLMError
from app.llm.client import LLMClient
from app.llm.stub import OfflineStubProvider
from app.pipeline import merit_claims as mc

CFG = mc.criterion_config("M1")

FORM = ("I rank 3rd in a cohort of 142 students, with a GPA of 3.84 out of 4.0. I received the Dean's Merit Award in 2023, 2024 and 2025.\n"
        "My project won first place at the National Student Innovation Showcase 2025.")
CERT = "Dean's Merit Award\nis proudly presented to the student, placing in the top 5% of the cohort for the 2025 academic year."
TEXTS = {"application_text": FORM, "document:c1": CERT}
LABELS = {"application_text": "Application form", "document:c1": "Certificate"}
KINDS = {"document:c1": "certificate"}


def stub_client() -> LLMClient:
    return LLMClient(OfflineStubProvider())


class Broken:
    model_name = "broken"
    stats: dict = {}

    def call_llm(self, prompt, schema):
        raise LLMError("down")


# ---------------------------------------------------------------- stage 1

def test_every_match_is_found_in_every_document_with_no_cap():
    passages = mc.find_passages(TEXTS, CFG)
    assert {p.source for p in passages} == {"application_text", "document:c1"}
    # every passage is a verbatim slice of its document, in document order
    for p in passages:
        assert TEXTS[p.source][p.start:p.end] == p.text
    starts = [p.start for p in passages if p.source == "application_text"]
    assert starts == sorted(starts)
    many = {"application_text": "\n".join(f"I won a prize in {2000 + i}." for i in range(40))}
    covered = " ".join(p.text for p in mc.find_passages(many, CFG))
    assert all(f"in {2000 + i}." in covered for i in range(40))   # nothing is cut off: every one of the 40 is in a passage


def test_a_sentence_broken_across_pdf_lines_stays_one_passage():
    text = ("In 2022 I founded a coding club for girls in rural schools and it now runs in\nsix schools with many mentors.\n"
            "Awards and publications\nFirst place, National Showcase 2025.")
    ps = mc.find_passages({"application_text": text}, mc.criterion_config("M3"))
    assert any("runs in\nsix schools" in p.text for p in ps)


# ---------------------------------------------------------------- stage 3

def _claim(quote, restatement=None, pid="p1", theme="grades"):
    return mc.ClaimOut(restatement=restatement or quote, theme=theme, source_quote=quote, passage_id=pid)


def test_a_quote_that_is_not_in_the_document_is_dropped_and_listed():
    ps = mc.find_passages(TEXTS, CFG)
    pid = next(p.passage_id for p in ps if p.source == "application_text")
    kept, dropped = mc.verify_claims([_claim("a GPA of 3.84 out of 4.0", pid=pid), _claim("a GPA of 3.99 out of 4.0", pid=pid)], ps, TEXTS, CFG)
    assert [c["quote"] for c in kept] == ["a GPA of 3.84 out of 4.0"]
    assert dropped and dropped[0]["reason"] == "The quote was not found in the document."


def test_a_number_in_the_restatement_must_be_in_the_quote():
    ps = mc.find_passages(TEXTS, CFG)
    pid = next(p.passage_id for p in ps if p.source == "application_text")
    kept, dropped = mc.verify_claims([_claim("GPA of 3.84 out of 4.0", restatement="GPA of 3.9 out of 4.0", pid=pid)], ps, TEXTS, CFG)
    assert not kept and dropped[0]["reason"] == "A number in the restatement is not in the quote."


def test_an_unknown_passage_is_dropped_and_the_same_quote_is_kept_once():
    ps = mc.find_passages(TEXTS, CFG)
    pid = next(p.passage_id for p in ps if p.source == "application_text")
    kept, dropped = mc.verify_claims([_claim("GPA of 3.84", pid="p99"), _claim("GPA of 3.84", pid=pid), _claim("GPA of 3.84", pid=pid)], ps, TEXTS, CFG)
    assert len(kept) == 1 and len(dropped) == 1


# ---------------------------------------------------------------- stage 4 checks

def _claims():
    award = {"subject": "Dean's Merit Award", "attribute": "year"}
    return [
        {"claim_id": "c1", "source": "application_text", "quote": "I received the Dean's Merit Award in 2023, 2024 and 2025.", "theme": "awards", "position": 0, **award},
        {"claim_id": "c2", "source": "document:c1", "quote": "Dean's Merit Award for the 2025 academic year.", "theme": "awards", "position": 0, **award},
        {"claim_id": "c3", "source": "application_text", "quote": "with a GPA of 3.84 out of 4.0", "theme": "grades", "position": 5, "subject": "applicant", "attribute": "GPA"},
        {"claim_id": "c4", "source": "document:c1", "quote": "she has a GPA of 3.84", "theme": "grades", "position": 0, "subject": "applicant", "attribute": "GPA"},
    ]


ORDER = {"application_text": 0, "document:c1": 1}
item = lambda theme, text, ids: mc.SummaryItemOut(theme=theme, text=text, claim_ids=ids)  # noqa: E731


def test_a_bullet_must_cite_existing_claims_and_use_only_numbers_from_them():
    raw = [item("grades", "The applicant reports a grade point average of 3.84.", ["c3"]),
           item("grades", "The applicant reports a GPA.", []),
           item("grades", "The applicant reports a GPA.", ["c99"]),
           item("grades", "The applicant reports a GPA of 3.9.", ["c3"])]
    kept, dropped = mc.check_items(raw, _claims(), CFG, ORDER)
    assert [i["claim_ids"] for i in kept] == [["c3"]]
    assert {d["reason"] for d in dropped} == {"The bullet cites no claim.", "The bullet cites a claim that does not exist.",
                                              "A number in the bullet is not in a cited quote."}


def test_a_bullet_that_copies_its_quote_is_rejected_by_the_overlap_rule():
    claims = _claims()
    copy = item("awards", "I received the Dean's Merit Award in 2023, 2024 and 2025.", ["c1"])
    fragment = item("awards", "Received the Dean's Merit Award in 2023.", ["c1"])
    cut = item("awards", "the dean's merit award in 2023, 2024", ["c1"])
    own = item("awards", "The form says the Dean's Merit Award was given in 2023, 2024 and 2025.", ["c1"])
    kept, dropped = mc.check_items([copy, fragment, cut, own], claims, CFG, ORDER)
    assert [k["text"] for k in kept] == [own.text]
    reasons = [d["reason"] for d in dropped]
    assert reasons.count("The summary line copies the quote instead of summarising it.") == 2
    assert "The summary line is not a full sentence." in reasons     # lower-cased, cut off: never shown


def test_overlap_limit_is_measured_not_guessed():
    q = "In 2022 I founded Code Pahadi, a free weekend coding club for girls in rural schools."
    assert mc.too_close("In 2022 I founded Code Pahadi, a free weekend coding club for girls in rural school.", [q])
    assert not mc.too_close("The applicant started a free coding club for girls in 2022.", [q])


def test_the_same_fact_in_two_documents_is_one_bullet_with_all_sources_and_is_not_called_a_difference():
    kept, _ = mc.check_items([item("grades", "Both the form and a letter give a GPA of 3.84.", ["c3", "c4"])], _claims(), CFG, ORDER)
    assert len(kept) == 1 and kept[0]["documents_also"] == ["document:c1"]
    assert kept[0]["differs"] is False      # one document simply says less: not a conflict


def test_the_same_award_in_different_years_is_a_real_difference():
    kept, _ = mc.check_items([item("awards", "The Dean's Merit Award is listed for 2023, 2024 and 2025.", ["c1", "c2"])], _claims(), CFG, ORDER)
    assert kept[0]["differs"] is True and kept[0]["claim_ids"] == ["c1", "c2"]


def test_two_referees_giving_their_own_length_of_association_is_not_a_difference():
    a = {"claim_id": "r1", "source": "document:ref1", "quote": "I have known her for 4 years (since August 2022).", "theme": "relationship",
         "position": 0, "subject": "Dr Rai", "attribute": "length of association"}
    b = {"claim_id": "r2", "source": "document:ref2", "quote": "a total of about 27 months (since June 2024).", "theme": "relationship",
         "position": 0, "subject": "Mr Thapa", "attribute": "length of association"}
    assert mc.differs([a, b]) is False                       # different subjects: never compared
    assert mc.differs([a, b | {"subject": "Dr Rai"}]) is True   # the same person, two lengths: a real conflict
    assert mc.differs([a | {"attribute": ""}, b | {"subject": "Dr Rai", "attribute": ""}]) is False   # no attribute: not compared


def test_different_non_year_numbers_in_two_documents_differ():
    assert mc._documents_differ([{"3.84"}, {"3.48"}]) is True
    assert mc._documents_differ([{"3.84", "142"}, {"3.84"}]) is False
    assert mc._documents_differ([{"2025"}, {"2025"}]) is False


def test_bullets_follow_the_theme_template_then_document_order_never_importance():
    raw = [item("awards", "The form lists a Dean's Merit Award for 2023, 2024 and 2025.", ["c1"]),
           item("grades", "The form gives a grade point average of 3.84.", ["c3"])]
    kept, _ = mc.check_items(raw, _claims(), CFG, ORDER)
    assert [i["theme"] for i in kept] == ["grades", "awards"]      # the template order of config/merit_criteria.yaml
    assert [i["item_id"] for i in kept] == ["b1", "b2"]


def test_the_overview_uses_only_numbers_from_the_quotes_and_never_copies_one():
    lines = ["The form and a certificate describe academic results.", "The applicant reports a GPA of 3.99.",
             "I received the Dean's Merit Award in 2023, 2024 and 2025.", "Referee letter 1 confirms a GPA of 3.84."]
    assert mc.check_overview(lines, _claims(), ["Referee letter 1"]) == [lines[0], lines[3]]


# ---------------------------------------------------------------- relevance: which documents feed a criterion

def test_supporting_evidence_reads_only_referee_letters_and_the_referee_lines_of_the_resume():
    texts = {"application_text": "I recommend myself. I founded a club and was its president.",
             "document:r1": "I have known Sita for four years and I recommend her without reservation.",
             "document:coe": "This document confirms the student is enrolled. Course start date 22/02/2027.",
             "document:fl": "Flight SQ 0211 to Darwin. Passengers must carry a valid passport.",
             "document:cv": "Founder of a coding club. Referees: Dr Anita Rai (HIT). Skills: Python and SQL.",
             "document:cert": "Dean's Merit Award for outstanding academic achievement."}
    kinds = {"document:r1": "referee letter", "document:coe": "coe", "document:fl": "travel booking", "document:cv": "resume", "document:cert": "certificate"}
    cfg = mc.criterion_config("M2")
    assert set(mc.relevant_texts(texts, kinds, cfg)) == {"document:r1", "document:cv"}
    passages = mc.find_passages(mc.relevant_texts(texts, kinds, cfg), cfg, kinds)
    cv = [p.text for p in passages if p.source == "document:cv"]
    assert cv and all("Referee" in t for t in cv)            # only the resume lines that name the referees
    r = mc.build_criterion(stub_client(), "M2", "Supporting evidence", texts, mc.plain_names(list(texts), kinds), "v1", kinds=kinds)
    assert not {c["source"] for c in r["claims"]} & {"application_text", "document:coe", "document:fl", "document:cert"}
    assert all(i["theme"].startswith("src:") for i in r["items"]), "referee criteria are grouped by who said it"


def test_the_forms_own_pdf_is_never_read_twice():
    texts = {"application_text": "I won a prize in 2025.", "document:twin": "I won a prize in 2025."}
    assert set(mc.relevant_texts(texts, {"document:twin": "application responses"}, CFG)) == {"application_text"}


def test_documents_get_plain_unique_names():
    kinds = {"document:a": "referee letter", "document:b": "referee letter", "document:c": "coe", "document:d": "application responses"}
    names = mc.plain_names(["application_text", "document:a", "document:b", "document:c", "document:d"], kinds)
    assert names == {"application_text": "Application form", "document:a": "Referee letter 1", "document:b": "Referee letter 2",
                     "document:c": "CoE", "document:d": "Application form"}


# ---------------------------------------------------------------- stage 5 and the whole thing

def test_whole_pipeline_cites_verified_quotes_and_lists_unused_passages():
    r = mc.build_criterion(stub_client(), "M1", "Academic merit", TEXTS, LABELS, "v1", kinds=KINDS)
    assert r["status"] == "ok" and r["items"]
    by_id = {c["claim_id"]: c for c in r["claims"]}
    for it in r["items"]:
        assert it["claim_ids"] and all(i in by_id for i in it["claim_ids"])
        for i in it["claim_ids"]:
            assert by_id[i]["quote"] in TEXTS[by_id[i]["source"]].replace("\n", " ") or mc.normalise(by_id[i]["quote"]) in mc.normalise(TEXTS[by_id[i]["source"]])
    for it in r["items"]:
        assert not mc.too_close(it["text"], [by_id[i]["quote"] for i in it["claim_ids"]]), "a bullet is never a copied quote"
    # coverage: every passage is either used by a bullet or listed as unused, and nothing the code found is hidden
    assert r["used_passages"] <= {p.passage_id for p in r["passages"]}
    assert {d["source"] for d in r["coverage"]} == set(TEXTS)


def test_an_unreadable_document_is_listed_in_the_coverage():
    r = mc.build_criterion(stub_client(), "M1", "Academic merit", TEXTS, LABELS, "v1", unreadable=["document:scan"], kinds=KINDS)
    assert {"source": "document:scan", "label": "document:scan", "passages": 0, "readable": False} in r["coverage"]


def test_when_the_ai_is_unavailable_the_passages_are_still_there_and_nothing_is_invented():
    r = mc.build_criterion(Broken(), "M1", "Academic merit", TEXTS, LABELS, "v1", kinds=KINDS)
    assert r["status"] == "failed" and r["items"] == [] and r["claims"] == [] and r["passages"]
    none = mc.build_criterion(None, "M1", "Academic merit", TEXTS, LABELS, "v1", kinds=KINDS)
    assert none["status"] == "no_ai" and none["passages"]


# ---------------------------------------------------------------- fairness: the same facts, written three ways

POLISHED = ("I graduated with distinction, ranking 3rd among 142 classmates, with a GPA of 3.84. I received the Dean's Merit Award in 2023, 2024 and 2025. "
            "In 2022 I founded Code Pahadi, which now serves 180 participants in 6 schools.")
PLAIN = ("I am rank 3rd in 142 students. My GPA is 3.84. I got the Dean's Merit Award in 2023, 2024 and 2025. "
         "In 2022 I founded Code Pahadi. It has 180 participants in 6 schools.")
SECOND = ("I am having rank 3rd from 142 student. GPA of me is 3.84. Dean's Merit Award I received in 2023, 2024 and 2025. "
          "In 2022 I am founding Code Pahadi, and 180 participants are in 6 schools.")


def _fact_set(text: str) -> set[tuple[str, frozenset[str]]]:
    """What was claimed, in a form that ignores wording: the theme and the numbers each verified claim carries."""
    out: set[tuple[str, frozenset[str]]] = set()
    for code in ("M1", "M3"):
        r = mc.build_criterion(stub_client(), code, "criterion", {"application_text": text}, {"application_text": "Application form"}, "v1")
        out |= {(code, frozenset(mc.numbers(c["quote"]))) for c in r["claims"] if mc.numbers(c["quote"])}
    return out


@pytest.mark.parametrize("variant", [PLAIN, SECOND])
def test_polished_plain_and_second_language_writing_give_the_same_facts(variant):
    # The offline stub is a keyword matcher, so this checks the pipeline keeps no wording-dependent step of its own: the facts
    # (the numbers and their themes) must be the same whichever way they are written. A real model needs the same test.
    base = {n for _c, n in _fact_set(POLISHED)}
    other = {n for _c, n in _fact_set(variant)}
    assert {"3", "142"} <= set().union(*other) and {"3.84"} <= set().union(*other)
    assert {n for n in other if "2023" in n} and {n for n in other if "180" in n or "6" in n}
    assert set().union(*base) == set().union(*other)


def test_a_redaction_token_is_not_a_number():
    assert mc.numbers("Letter for [PERSON_1] about [LOCATION_12], GPA 3.84") == {"3.84"}
    a = {"claim_id": "a", "source": "application_text", "quote": "[PERSON_1] won the Showcase 2025.", "subject": "Showcase", "attribute": "year"}
    b = {"claim_id": "b", "source": "document:x", "quote": "[PERSON_3] won the 2025 Showcase.", "subject": "Showcase", "attribute": "year"}
    assert mc.differs([a, b]) is False
