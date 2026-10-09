"""Check 1 (cross_document): plain code, every flag quotes the lines it compares."""

from datetime import date

from app.pipeline.consistency import cross_document as cd
from tests.consistency.conftest import ctx, doc

COE = ("Provider: Charles Darwin University\nCourse: Bachelor of Nursing\nCourse Start Date: 16 November 2026\n"
       "Course End Date: 15 November 2029\nCourse Duration: 3 years (6 semesters)\nDate CoE Issued: 14 September 2026\n")


def booking(arrive: str) -> str:
    return f"Flight Booking Confirmation\nArrival: Darwin (DRW) {arrive} 06:05\n"


def test_arrival_after_the_course_starts_is_flagged_with_both_lines_quoted():
    c = ctx([doc("c", "coe", COE), doc("b", "travel_booking", booking("14 December 2026"))])
    (f,) = cd.arrival_vs_start(c)
    assert f.check_id == "cross_document.arrival_vs_start" and f.strength == "strong"
    quotes = [e.quote for e in f.evidence]
    assert "Course Start Date: 16 November 2026" in quotes and any("14 December 2026" in q for q in quotes)
    assert all(e.verified for e in f.evidence if e.kind == "quote")


def test_a_short_late_arrival_is_weak_and_a_normal_arrival_is_not_flagged():
    (weak,) = cd.arrival_vs_start(ctx([doc("c", "coe", COE), doc("b", "travel_booking", booking("22 November 2026"))]))
    assert weak.strength == "weak"
    assert cd.arrival_vs_start(ctx([doc("c", "coe", COE), doc("b", "travel_booking", booking("2 November 2026"))])) == []


def test_an_arrival_months_before_the_start_is_a_weak_signal():
    (f,) = cd.arrival_vs_start(ctx([doc("c", "coe", COE), doc("b", "travel_booking", booking("1 June 2026"))]))
    assert f.strength == "weak" and "much earlier" in f.description


def test_typed_dates_that_disagree_with_the_documents_are_flagged():
    c = ctx([doc("c", "coe", COE), doc("b", "travel_booking", booking("2 November 2026"))],
            fields={"course_start_date": "2026-11-23", "course_end_date": "2029-11-15", "arrival_date": "2026-11-02"})
    ids = {f.check_id for f in cd.typed_vs_documents(c)}
    assert ids == {"cross_document.typed_coe_start"}


def test_typed_dates_that_match_in_another_format_are_not_flagged():
    c = ctx([doc("c", "coe", COE)], fields={"course_start_date": "16/11/2026", "course_end_date": "15 November 2029"})
    assert cd.typed_vs_documents(c) == []


def test_provider_and_course_on_the_form_must_match_the_coe():
    c = ctx([doc("c", "coe", COE)], fields={"education_provider": "Darwin City College", "course_name": "Master of Law"})
    assert {f.check_id for f in cd.typed_vs_documents(c)} == {"cross_document.provider", "cross_document.course"}


def test_coe_dates_that_do_not_fit_the_stated_length():
    bad = COE.replace("15 November 2029", "30 November 2031")
    (f,) = cd.coe_length(ctx([doc("c", "coe", bad)]))
    assert f.check_id == "cross_document.coe_length" and "5.0 years" in f.description and "3.0 years" in f.description
    assert cd.coe_length(ctx([doc("c", "coe", COE)])) == []


LETTER = "To whom it may concern,\nI have known [PERSON_1] for three years.\nDate: {d}\nKnown applicant for: 3 years\n"


def test_referee_letters_outside_2024_to_2026_or_after_submission():
    old = cd.referee_dates(ctx([doc("l", "referee_letter", LETTER.format(d="18 May 2022"))]))
    assert [f.check_id for f in old] == ["cross_document.referee_date_window"]
    future = cd.referee_dates(ctx([doc("l", "referee_letter", LETTER.format(d="30 October 2026"))]))
    assert [f.check_id for f in future] == ["cross_document.referee_date_future"]
    older = cd.referee_dates(ctx([doc("l", "referee_letter", LETTER.format(d="3 March 2024"))], submitted=date(2026, 10, 12)))
    assert [(f.check_id, f.strength) for f in older] == [("cross_document.referee_date_old", "weak")]
    assert cd.referee_dates(ctx([doc("l", "referee_letter", LETTER.format(d="3 June 2026"))])) == []


def test_known_for_five_years_against_the_letters_own_timeline():
    text = ("To the Panel,\nI have known [PERSON_1] for five years. She first joined my research group in January 2026 and has "
            "done well.\nDate: 10 September 2026\nKnown applicant for: 5 years\n")
    (f,) = cd.known_for_vs_timeline(ctx([doc("l", "referee_letter", text)]))
    assert f.check_id == "cross_document.known_for_vs_timeline" and f.strength == "strong"
    assert len([e for e in f.evidence if e.kind == "quote"]) == 2


def test_a_consistent_letter_is_not_flagged():
    text = ("To the Panel,\nI have known [PERSON_1] for three years, since September 2023.\nDate: 10 September 2026\n"
            "Known applicant for: 3 years\n")
    assert cd.known_for_vs_timeline(ctx([doc("l", "referee_letter", text)])) == []


def test_a_letter_about_a_different_name_is_flagged():
    text = "RE: Letter of support for [PERSON_2], 2026 Scholarship\nI recommend this student.\nDate: 1 June 2026\n"
    c = ctx([doc("l", "referee_letter", text)], applicant_token="[PERSON_1]")
    assert [f.check_id for f in cd.letter_subject(c)] == ["cross_document.letter_subject"]
    assert cd.letter_subject(ctx([doc("l", "referee_letter", text)], applicant_token="[PERSON_2]")) == []


def test_consistent_documents_raise_nothing():
    c = ctx([doc("c", "coe", COE), doc("b", "travel_booking", booking("2 November 2026")),
             doc("l", "referee_letter", LETTER.format(d="3 June 2026"))],
            fields={"course_start_date": "2026-11-16", "arrival_date": "2026-11-02", "education_provider": "Charles Darwin University",
                    "course_name": "Bachelor of Nursing"})
    assert cd.run(c) == []
