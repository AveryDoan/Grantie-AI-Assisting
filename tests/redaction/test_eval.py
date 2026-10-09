"""Step 8: the evaluation CLI, findings stability (test 12) and the fixes it led to."""

from __future__ import annotations

import pytest

pytest.importorskip("presidio_analyzer")
pytest.importorskip("en_core_web_lg")

from redaction import eval as redaction_eval  # noqa: E402
from redaction.detector import Detector  # noqa: E402


@pytest.fixture(scope="module")
def results():
    return redaction_eval.run(twins=True, names=True)


def test_no_labelled_value_would_reach_the_llm(results):
    present = [v for v in results.values if v.present]
    assert len(present) >= 90
    assert [v for v in present if v.exposed] == []
    assert all(v.present for v in results.values), [v.value for v in results.values if not v.present]  # labels match inputs


def test_terms_the_rules_need_are_not_removed(results):
    assert [k.term for k in results.keeps if k.removed] == []


def test_findings_do_not_change_with_redaction_on_the_twin_set(results):
    """Test 12: N01/N08/N09 (same facts, three writing styles) get the same findings before and after redaction."""
    for code, r in results.twins.items():
        assert r["before"] == r["after"], code
    styles = list(results.twins.values())
    assert styles[0]["after"] == styles[1]["after"] == styles[2]["after"]


def test_known_values_cover_every_name_in_every_culture(results):
    assert all(n["known"] == "full" for n in results.names)


def test_report_is_written_and_honest(results, tmp_path):
    md = redaction_eval.markdown(results)
    for heading in ("## Summary", "## Results by entity type", "## Every miss", "## Over-redaction",
                    "## Name recall by culture", "## Findings before and after redaction", "## Known limits"):
        assert heading in md
    assert "Synthetic data only" in md and "not a held-out test" in md
    assert "Le Hoang Nam" in md  # the known detector-only miss is listed, not hidden


# ---------------------------------------------------------------- fixes found by the evaluation


@pytest.fixture(scope="module")
def detector() -> Detector:
    return Detector()


def test_sentence_initial_non_english_name_is_covered(detector):
    text = "Sione Tupou and Losana Ratu run the youth group."
    assert {text[d.start:d.end] for d in detector.detect(text)} == {"Sione Tupou", "Losana Ratu"}


def test_sentence_initial_english_word_is_not_taken_as_a_name(detector):
    text = "Yesterday Tupou called me."
    assert [text[d.start:d.end] for d in detector.detect(text)] == ["Tupou"]


def test_nt_abbreviation_is_kept(detector):
    assert detector.detect("I want study nursing in NT and after work.") == []


def test_a_title_is_not_redacted_as_part_of_a_known_name(detector):
    from redaction.recognizers import KnownValue

    text = "title: Ms\nReferee name: Ms Rosa Example\nI thank Rosa."
    found = {text[d.start:d.end] for d in detector.detect(text, [KnownValue("Ms Rosa Example", "REFEREE", "document")])}
    assert "Ms" not in found and "Rosa" in found and any("Rosa Example" in f for f in found)
