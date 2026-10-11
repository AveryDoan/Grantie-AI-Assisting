"""sandbox assess: an application built from a folder of FAKE documents. Nothing here is real."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("presidio_analyzer")
pytest.importorskip("en_core_web_lg")

from redaction import sandbox, sandbox_assess  # noqa: E402
from redaction.crypto import generate_key  # noqa: E402

NAME, DOB = "Mira Vellamore", "1999/07/14"
FILES = {
    "01_Application_Responses.txt": (
        "Application Responses\nApplicant: Mira Vellamore | Date of birth: 14 July 1999\nMobile: +60 12 345 6789\n"
        "What are you studying now, and where?\nI am in the final semester of a Bachelor of Engineering in Zenith Harbour.\n"
        "Academic achievements\nI rank 3rd of 142 students with a GPA of 3.84 out of 4.0.\n"
        "Leadership\nI founded a free coding club for girls and now lead 14 volunteer mentors.\n"
        "How will studying in the NT contribute to your future?\nI want to build data skills for remote communities.\n"),
    "02_Confirmation_of_Enrolment.txt": (
        "Charles Darwin University\nCONFIRMATION OF ENROLMENT\nConfirmation of Enrolment (CoE) number E0912345678\n"
        "Student ID S2026-04417\nFamily name VELLAMORE\nGiven names MIRA SOLENNE\nDate of birth 14/07/1999\nCountry of citizenship Utopia\n"
        "Residential address (home country) 22 Example Lane, Zenith Harbour, Utopia\n"
        "Course Master of Data Science (SDASC3)\nMode of study Full time, on campus\nCourse start date 22/02/2027\n"),
    "03_Flight_Booking.txt": "Travel Itinerary\nBooking reference XK7R2P\nPassenger VELLAMORE / MIRA\nArrival in Darwin: Saturday 13 February 2027, 03:50\n",
    "04_Letter_of_Support_1.txt": ("To the Assessment Panel,\nI have known Mira Vellamore for four years. I recommend her without reservation.\n"
                                  "Yours sincerely,\nDr Anil Teacher\nLength of association: 4 years\nEmail: anil.teacher@example.invalid\n"),
    "05_Letter_of_Support_2.txt": ("To the Assessment Panel,\nI supervised Mira Vellamore for 27 months. I recommend her fully.\n"
                                  "Yours sincerely,\nMr Ben Manager\nLength of association: 27 months\n"),
    "07_Resume.txt": "Mira Vellamore\nEducation\nBachelor of Engineering\nReferees\nDr Anil Teacher, Mr Ben Manager\n",
}
PERSONAL = ["Vellamore", "Mira", "Solenne", "Teacher", "Manager", "Zenith", "E0912345678", "S2026-04417", "anil.teacher", "345 6789"]


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("REDACTION_KEY", generate_key())
    monkeypatch.setenv("GRANTIE_SANDBOX_DIR", str(tmp_path / "sbx"))
    d = tmp_path / "docs"
    d.mkdir()
    for name, text in FILES.items():
        (d / name).write_text(text, encoding="utf-8")
    answers = tmp_path / "answers"
    answers.mkdir()
    (answers / "community_engagement.txt").write_text("I run a free coding club and mentor younger students.", encoding="utf-8")
    return {"docs": d, "answers": answers, "out": tmp_path / "sbx" / "output"}


def run(folder, **kw):
    lines: list[str] = []
    out = sandbox_assess.assess_folder(folder["docs"], [NAME, DOB], answers_dir=folder["answers"], out_root=folder["out"],
                                       echo=lines.append, **kw)
    return out, "\n".join(lines)


def test_application_is_built_from_the_documents_and_assessed_locally(folder):
    out, stdout = run(folder)
    meta = json.loads((out / "meta.json").read_text())
    assert meta["ai_status"] == "ready" and sum(meta["statuses"].values()) == 28
    report = (out / "app_report.html").read_text()
    for needle in ("What the AI reads", "The result, rule by rule", "your application-responses file",
                   "written for the sample", "your Confirmation of Enrolment", "OFFICER-ONLY"):
        assert needle in report, needle
    assert "<td>(home country)" not in report and "<td>22 Example Lane" in report  # the label is not part of the address field
    assert "S2" in report and "22 February 2027" in report  # the course starts outside Round 1
    assert 'chip bad">Not met' in report
    assert "2 referee letters were uploaded" in report  # the résumé is not counted as a letter
    # The officer-only report restores the applicant's own words; nothing else holds them.
    assert "Vellamore" in report
    for name in ("meta.json", "token_map.enc"):
        text = (out / name).read_text()
        for v in PERSONAL:
            assert v.casefold() not in text.casefold(), (name, v)
    for v in PERSONAL:
        assert v.casefold() not in stdout.casefold(), v


def test_a_coe_number_is_now_recognised_on_its_own_so_the_ai_is_not_blocked(folder):
    # Before the CoE / student ID / booking reference patterns were added, this number format leaked and blocked the AI
    # until the officer supplied it. Those formats are now recognised without help.
    out, stdout = run(folder)
    meta = json.loads((out / "meta.json").read_text())
    assert "long_digit_string" not in meta["leak_without_ids"]
    assert not meta["leak_with_ids"]
    assert "the AI would be blocked" not in stdout


def test_words_the_detector_mistakes_for_people_can_be_marked_by_an_officer(folder):
    out, stdout = run(folder, not_personal=["Email"])
    assert out.name.endswith("-reviewed") and "Officer marked as not personal" in stdout
    assert "Email" in json.loads((out / "meta.json").read_text())["marked_not_personal"]
    assert "marked these ordinary words as NOT personal" in (out / "app_report.html").read_text()


def test_unreadable_known_values_stop_early(folder):
    with pytest.raises(sandbox.SandboxError, match="name and date of birth"):
        sandbox_assess.assess_folder(folder["docs"], ["Mira Vellamore"], out_root=folder["out"], echo=lambda *_: None)
