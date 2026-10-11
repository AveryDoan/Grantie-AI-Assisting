"""The synthetic cases F01 to F10 and the evaluation harness. Everything is fictional."""

import json
import re
from pathlib import Path

import pytest

from eval import consistency as ev
from seed.fraud.generate import ROOT, write_case
from seed.fraud.load import expected
from seed.fraud.scenarios import build
from tests.consistency.conftest import flags_of, raised

CASES = build()
# The ACMA's numbers reserved for fiction: 29 mobiles, and every number in these geographic ranges.
ACMA_MOBILES = {"0491570006", "0491570156", "0491570157", "0491570158", "0491570159", "0491570110", "0491570313", "0491570737",
                "0491571266", "0491571491", "0491571804", "0491572549", "0491572665", "0491572983", "0491573770", "0491573087",
                "0491574118", "0491574632", "0491575254", "0491575789", "0491576398", "0491576801", "0491577426", "0491577644",
                "0491578957", "0491578148", "0491578888", "0491579212", "0491579760", "0491579455"}
ACMA_LANDLINE = re.compile(r"^0[2378](?:5550|7010)\d{4}$")


def test_there_are_ten_cases_with_the_expected_files():
    assert [c.code for c in CASES] == [f"F{i:02d}" for i in range(1, 11)]
    for c in CASES:
        folder = ROOT / c.code
        assert (folder / "form.json").exists() and (folder / "expected_flags.json").exists() and list(folder.glob("*.pdf")), c.code
        exp = json.loads((folder / "expected_flags.json").read_text())
        assert set(exp) == {"code", "control", "should_raise", "may_raise", "should_not_raise"} and exp["should_raise"] == c.should


def test_generated_files_are_exactly_what_the_script_produces(tmp_path):
    for c in CASES:
        folder = write_case(c, tmp_path)
        for f in folder.iterdir():
            assert f.read_bytes() == (ROOT / c.code / f.name).read_bytes(), f"{c.code}/{f.name}: regenerate with python -m seed.fraud.generate"
        assert {f.name for f in folder.iterdir()} == {f.name for f in (ROOT / c.code).iterdir()}


def test_everything_is_fictional_example_emails_and_reserved_phone_numbers():
    text = []
    for c in CASES:
        text.append(json.dumps({"f": c.fields, "a": c.answers, "e": c.email}))
        text.extend("\n".join(d.lines) + json.dumps(d.info or {}) for d in c.docs)
    blob = "\n".join(text)
    emails = set(re.findall(r"[\w.+-]+@([\w.-]+)", blob))
    assert emails and all(d.endswith(("example.com", "example.org")) for d in emails), emails
    phones = {re.sub(r"\D", "", m) for m in re.findall(r"(?<![\w/])(?:\+\d[\d ()-]{7,}\d|0\d[\d ()-]{7,}\d)", blob)}
    assert phones
    for p in phones:
        national = ("0" + p[2:]) if p.startswith("61") else p
        assert national in ACMA_MOBILES or ACMA_LANDLINE.match(national), p
    assert "SAMPLE DOCUMENT" in blob and not re.search(r"(?i)\bgov\.au\b|\.edu\.au", blob)


def test_the_biographies_meet_the_150_word_rule_and_every_case_is_complete():
    for c in CASES:
        assert 120 <= len(c.answers["biography"].split()) <= 180, (c.code, len(c.answers["biography"].split()))
        assert {"applicant_name", "date_of_birth", "course_start_date", "arrival_date", "declaration_agreed"} <= set(c.fields)
        kinds = [d.kind for d in c.docs]
        assert "coe" in kinds and "travel booking" in kinds and kinds.count("referee letter") == 2 and "headshot" in kinds, c.code


def test_pdf_metadata_in_the_files_is_what_the_scenarios_planted():
    from pypdf import PdfReader

    f06 = PdfReader(str(ROOT / "F06" / "referee_letter_2.pdf")).metadata
    assert f06.producer == "iLovePDF" and "Photoshop" in f06.creator and f06.creation_date.year == 2026
    assert PdfReader(str(ROOT / "F01" / "coe_1.pdf")).metadata is None


# ---------------------------------------------------------------- what the checks do with them (offline stub)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.code)
def test_each_planted_problem_is_found_and_nothing_unexpected_is_raised(pool, case):
    got = raised(pool, case.code)
    assert set(case.should) <= got, f"{case.code} missed {set(case.should) - got}"
    unexpected = got - set(case.should) - set(case.allow)
    assert not unexpected, f"{case.code} unexpected {unexpected}"


def test_the_controls_raise_no_strong_flag(pool):
    for code in ("F10", "N08"):
        assert not [f for f in flags_of(pool, code) if f["strength"] == "strong"], code
    assert flags_of(pool, "N08") == []                         # the clean existing case raises nothing at all
    assert {f["strength"] for f in flags_of(pool, "F10")} == {"weak"}
    assert {f["strength"] for f in flags_of(pool, "F06")} == {"weak"}


def test_each_case_demonstrates_its_check_type(pool):
    by_case = {"F01": "cross_document", "F02": "cross_document", "F03": "cross_document", "F04": "timeline", "F05": "narrative",
               "F06": "document_integrity", "F07": "cross_application", "F08": "cross_application", "F09": "cross_application"}
    for code, kind in by_case.items():
        assert any(f["check_type"] == kind for f in flags_of(pool, code)), (code, kind)


# ---------------------------------------------------------------- the evaluation harness


def test_the_harness_scores_a_run_and_keeps_both_providers_in_one_report(tmp_path):
    from app.llm import LLMClient
    from app.llm.stub import OfflineStubProvider

    results, stats = ev.run_cases(LLMClient(OfflineStubProvider(), temperature=0.0), echo=lambda *_: None)
    assert stats["failed"] == [] and len(results) == 11
    table = ev.by_type(results)
    assert all(v["missed"] == 0 for v in table.values()) and sum(v["expected"] for v in table.values()) == 23
    out = tmp_path / "report.md"
    ev.write("stub", ev.section("stub", "offline keyword stub (NOT an LLM)", results, stats, ["note"]), out)
    ev.write("gemini", ev.section("gemini", "Gemini (test)", results, stats, ["note"]), out)
    ev.write("stub", ev.section("stub", "offline keyword stub (NOT an LLM)", results, stats, ["second note"]), out)   # replaces, not appends
    text = out.read_text()
    assert text.count("## Run: offline keyword stub") == 1 and text.count("## Run: Gemini (test)") == 1
    assert "second note" in text and text.index("offline keyword stub") < text.index("Gemini (test)")
    assert "written alongside the checks" in text and "Controls (precision)" in text
    assert not re.search(r"(?i)\b(fraud|fake|guilty|suspicious)\b", text)
