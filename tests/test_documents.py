"""Document classification and field reading (synthetic text only)."""

from __future__ import annotations


def test_a_stray_word_does_not_make_a_document_a_referee_letter():
    from app.pipeline.documents import classify

    footer = "SAMPLE DOCUMENT - Student, referee and booking details are fictional."
    resume = "Sita Example\nEducation\nBachelor of Engineering\nReferees\nDr A. Teacher, Mr B. Boss\n" + footer
    certificate = "Dean's Merit Award is proudly presented to Sita Example for outstanding academic achievement.\n" + footer
    assert classify(resume) == "other" and classify(certificate) == "other"
    letter = "To the Assessment Panel,\nI have known Sita Example for four years. I recommend her without reservation.\nYours sincerely,\nDr A. Teacher\nLength of association: 4 years"
    assert classify(letter) == "referee_letter"
    assert classify("I am writing this letter of support.\nI supervised her for 27 months.") == "referee_letter"


def test_fields_are_read_without_colons_and_the_letterhead_names_the_provider():
    from app.pipeline.documents import extract_fields

    coe = ("[page 1]\nCharles Darwin University\nCRICOS Provider Code: 00300K\nCONFIRMATION OF ENROLMENT\n"
           "Course Master of Data Science (SDASC3)\nCourse level Masters Degree (Coursework)\nMode of study Full time, on campus\n"
           "Course start date 22/02/2027\nCourse end date 20/11/2028\n")
    f = extract_fields(coe)
    assert f["course_name"] == "Master of Data Science (SDASC3)"  # not "level ..." from the next line
    assert f["study_load"] == "Full time, on campus" and f["course_start_date_iso"] == "2027-02-22"
    assert f["provider_name"] == "Charles Darwin University"
    assert extract_fields("Arrival in Darwin: Saturday 13 February 2027, 03:50 local time.")["arrival_date_iso"] == "2027-02-13"
    # ordinary sentences are not labels
    assert extract_fields("Course materials were sent to the student on time.") == {}
