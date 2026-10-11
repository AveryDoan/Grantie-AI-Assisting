"""Checks 2 (timeline) and 5 (narrative): the AI may only point at text; code verifies every quote."""

from datetime import date

import pytest

from app.llm import LLMError
from app.pipeline.consistency import narrative, timeline
from app.pipeline.consistency.llm_checks import Contradiction, NarrativeOut, TimelineEvent, TimelineOut, safe_topic
from app.pipeline.consistency.models import Evidence, Flag, flag
from tests.consistency.conftest import ctx, doc

FORM = "applicant_name: [PERSON_1]\ncurrent_study: I studied engineering for three years at a technical institute.\n"
RESUME = ("RESUME\nFounder and President, Youth Robotics Club, March 2013 - present\n"
          "Sales Associate, Example Retail Co, January 2024 - present, full-time\n"
          "Data Entry Clerk, Sample Logistics Pvt Ltd, March 2024 - present, full-time\n")
TRANSCRIPT = "OFFICIAL ACADEMIC TRANSCRIPT\nProgram: Bachelor of Engineering\nYears completed: 1\nStatus: Withdrawn after the first year\n"


class FakeGuard:
    """Stands in for GuardedLLM: builds the combined text and returns scripted AI output."""

    def __init__(self, out=None, error=False):
        self.out, self.error, self.calls = out, error, 0

    def combine(self, parts):
        text, segs, pos = "", [], 0
        for label, body in parts:
            head = f"=== {label} ===\n"
            segs.append((label, pos + len(head), pos + len(head) + len(body), body))
            text += head + body + "\n\n"
            pos = len(text)
        return text, segs

    def call_llm(self, prompt, schema):
        self.calls += 1
        if self.error:
            raise LLMError("scripted failure")
        return self.out


def event(label, start, end, quote, kind="role", ft=None):
    return TimelineEvent(label=label, kind=kind, start=start, end=end, full_time=ft, quote=quote)


def timeline_ctx(events, **kw):
    return ctx([doc("r", "other", RESUME, label="Resume")], form_text=FORM, guard=FakeGuard(TimelineOut(events=events)),
               fields={"date_of_birth": "2004-06-10"}, submitted=date(2026, 10, 12), **kw)


def test_timeline_flags_a_role_that_starts_before_the_applicant_could_have_held_it():
    c = timeline_ctx([event("Founder and President, Youth Robotics Club", "2013-03", "present",
                            "Founder and President, Youth Robotics Club, March 2013 - present")])
    trace = {}
    flags = timeline.run(c, trace)
    (f,) = [f for f in flags if f.check_id == "timeline.role_before_age"]
    assert f.strength == "strong" and "years old" in f.description
    assert all(e.verified for e in f.evidence if e.kind == "quote") and trace["verified"] == 1 and trace["dropped_unverified"] == 0
    assert any(e.kind == "field" and e.field == "date_of_birth" and e.value is None for e in f.evidence)   # the date itself is never shown


def test_timeline_flags_overlapping_full_time_work_and_study():
    c = timeline_ctx([
        event("Sales Associate", "2024-01", "present", "Sales Associate, Example Retail Co, January 2024 - present, full-time", ft=True),
        event("Data Entry Clerk", "2024-03", "present", "Data Entry Clerk, Sample Logistics Pvt Ltd, March 2024 - present, full-time", ft=True)])
    flags = timeline.run(c, {})
    assert [f.check_id for f in flags] == ["timeline.overlapping_full_time"]
    assert len([e for e in flags[0].evidence if e.kind == "quote"]) == 2


def test_part_time_work_and_sequential_roles_are_not_flagged():
    c = timeline_ctx([event("Sales Associate", "2024-01", "2025-01", "Sales Associate, Example Retail Co, January 2024 - present, full-time", ft=True),
                      event("Data Entry Clerk", "2025-03", "present", "Data Entry Clerk, Sample Logistics Pvt Ltd, March 2024 - present, full-time", ft=True)])
    assert timeline.run(c, {}) == []


def test_dates_that_run_backwards_are_flagged():
    c = timeline_ctx([event("Sales Associate", "2025-01", "2024-01", "Sales Associate, Example Retail Co, January 2024 - present, full-time")])
    assert "timeline.dates_backwards" in {f.check_id for f in timeline.run(c, {})}


def test_an_event_whose_quote_is_not_in_the_text_is_dropped_and_counted():
    c = timeline_ctx([event("Chief Executive", "2010-01", "present", "Chief Executive of a large company from January 2010 to present")])
    trace = {}
    assert timeline.run(c, trace) == []                       # no flag is built from an invented passage
    assert trace["dropped_unverified"] == 1 and trace["verified"] == 0
    assert trace["returned"][0]["verified"] is False


def test_visa_granted_before_the_coe_was_issued():
    visa = doc("v", "visa", "Visa Grant Notice\nDate of Grant: 3 August 2026\n")
    coe = doc("c", "coe", "Provider: Charles Darwin University\nDate CoE Issued: 14 September 2026\n")
    (f,) = timeline.visa_before_coe(ctx([visa, coe]))
    assert f.check_id == "timeline.visa_before_coe" and "42 days" in f.description
    later = doc("v", "visa", "Visa Grant Notice\nDate of Grant: 28 September 2026\n")
    assert timeline.visa_before_coe(ctx([later, coe])) == []


# ---------------------------------------------------------------- narrative


def narrative_ctx(contradictions, **kw):
    return ctx([doc("t", "other", TRANSCRIPT, label="Transcript")], form_text=FORM,
               guard=FakeGuard(NarrativeOut(contradictions=contradictions)), **kw)


GOOD = Contradiction(topic="years of study", first_quote="I studied engineering for three years at a technical institute.",
                     second_quote="Years completed: 1")


def test_narrative_contradiction_with_two_verified_quotes_becomes_a_flag():
    trace = {}
    (f,) = narrative.run(narrative_ctx([GOOD]), trace)
    assert f.check_id == "narrative.conflicting_statements" and f.strength == "strong" and f.verification == "verified"
    assert [e.source for e in f.evidence] == ["application_text", "document:t"]
    assert all(e.verified for e in f.evidence) and trace["verified"] == 1


def test_an_unverified_quote_is_dropped_and_the_item_becomes_unclear_with_no_quotes():
    invented = Contradiction(topic="years of study", first_quote="I studied engineering for three years at a technical institute.",
                             second_quote="The transcript shows the student failed every unit")
    trace = {}
    (f,) = narrative.run(narrative_ctx([invented]), trace)
    assert f.verification == "unclear" and f.evidence == [] and f.strength == "weak"
    assert "could not be found" in f.description and "failed" not in f.description    # the invented words are not repeated
    assert trace["dropped_unverified"] == 1 and trace["verified"] == 0


def test_the_same_passage_twice_is_not_a_contradiction():
    twice = Contradiction(topic="years of study", first_quote="Years completed: 1", second_quote="Years completed: 1")
    (f,) = narrative.run(narrative_ctx([twice]), {})
    assert f.verification == "unclear"


def test_a_failing_llm_gives_no_flags_and_never_raises():
    c = ctx([doc("t", "other", TRANSCRIPT)], form_text=FORM, guard=FakeGuard(error=True))
    trace = {}
    assert narrative.run(c, trace) == [] and trace["error"] == "LLMError"
    assert timeline.run(c, {}) == []


def test_without_an_ai_the_ai_checks_are_skipped():
    c = ctx([doc("t", "other", TRANSCRIPT)], form_text=FORM, guard=None)
    trace = {}
    assert narrative.run(c, trace) == [] and "skipped" in trace


def test_the_ais_topic_words_are_never_copied_into_a_flag_unchecked():
    assert safe_topic("an accusation of fraud") == "a detail"
    assert safe_topic("years of study") == "years of study"
    assert safe_topic("<script>") == "a detail"


# ---------------------------------------------------------------- the model itself enforces the principles


def test_an_ai_assisted_flag_cannot_exist_without_verified_quotes():
    with pytest.raises(ValueError, match="verified quotes"):
        flag("narrative.x", "narrative", "strong", "Two statements do not fit together.",
             [Evidence(kind="quote", source="application_text", label="a", quote="q", verified=False)])
    with pytest.raises(ValueError, match="verified quotes"):
        flag("timeline.x", "timeline", "strong", "Dates do not fit together.", [])


def test_document_signals_can_never_be_strong():
    with pytest.raises(ValueError, match="always weak"):
        flag("document_integrity.x", "document_integrity", "strong", "A signal.", [])


@pytest.mark.parametrize("word", ["fraud", "Fake", "GUILTY", "suspicious"])
def test_a_flag_cannot_accuse(word):
    with pytest.raises(ValueError, match="never accuse"):
        Flag(check_id="x.y", type="cross_document", strength="strong", description=f"This is {word}.", evidence=[])


def test_a_flag_has_no_score_rating_or_recommendation_field():
    assert not {"score", "risk", "rating", "rank", "recommendation", "verdict", "probability"} & set(Flag.model_fields)
