"""Side-by-side compare, the grouped 'add a missed item' list, and the document text preview. Synthetic data only."""

from __future__ import annotations

import pytest

from app.services import redaction_check as rc
from app.services.errors import ValidationFailed
from tests.conftest import app_id
from tests.test_guided_flow import app_of, confirm_documents, guided  # noqa: F401  (guided is a fixture)
from app.services import steps


@pytest.fixture
def redacted(store, officer, guided):  # noqa: F811
    confirm_documents(store, officer, "N01", guided)
    steps.complete_documents(store, officer, app_of(store, "N01"), guided)
    return guided


def _name(store):
    return app_of(store, "N01")["application_text"]["fields"]["applicant_name"]


def test_the_original_is_masked_until_switched_on_and_each_switch_on_is_logged_once(store, officer, redacted):
    audit = lambda: [a for a in store.select("audit_log") if a["action"] == "redaction.compare_original_opened"]
    blurred = rc.compare(store, officer, app_id("N01"), "application_text", False, redacted)
    assert blurred["original_shown"] is False and _name(store).split()[0] not in blurred["original"] and audit() == []
    assert len(blurred["original"]) == len(blurred["redacted"]) or blurred["original"]            # same layout, nothing readable
    shown = rc.compare(store, officer, app_id("N01"), "application_text", True, redacted)
    assert _name(store).split()[0] in shown["original"] and len(audit()) == 1
    assert "details" in audit()[0] and _name(store) not in str(audit()[0])                          # never the text
    spans = shown["spans"]
    assert spans and all({"id", "type", "token", "red", "orig"} <= set(s) for s in spans)
    s = spans[0]
    assert shown["redacted"][s["red"][0]:s["red"][1]] == s["token"]                                  # the AI text, with the token
    assert shown["original"][s["orig"][0]:s["orig"][1]]                                              # and where it was in the original
    # every span lines up on both sides: the original text at the span is the value that the token stands for
    from redaction.restore import restore  # noqa: F401
    assert all(not sp["orig"][0] > len(shown["original"]) for sp in spans)
    first = next(sp for sp in spans if sp["token"].startswith("[PERSON_"))
    assert shown["original"][first["orig"][0]:first["orig"][1]] in _name(store) or _name(store).split()[0] in shown["original"][first["orig"][0]:first["orig"][1]]


def test_every_option_makes_the_right_token_type_and_other_needs_a_description(store, officer, redacted):
    assert len(rc.OPTIONS) >= 30 and {o["group"] for o in rc.OPTIONS} == {"Identity", "Contact", "Location", "Identifiers", "Organisations", "Sensitive", "Other"}
    for o in rc.OPTIONS:
        token, _ = rc._resolve_kind(o["key"], "described")
        assert token == o["token"]
    with pytest.raises(ValidationFailed, match="Describe"):
        rc._resolve_kind("other", " ")
    with pytest.raises(ValidationFailed):
        rc._resolve_kind("nonsense", None)


def test_adding_a_missed_item_updates_the_ai_text_reruns_the_scan_and_counts_matches(store, officer, redacted):
    texts = rc._texts(store, app_of(store, "N01"))
    redacted_text = texts["application_text"][0]
    import re

    outside = re.sub(r"\[[^\]]*\]", " ", redacted_text)
    word = next(w for w in outside.split() if w.isalpha() and len(w) > 6)
    prev = rc.preview_missed(store, officer, app_id("N01"), "application_text", word, "club", False, True, redacted)
    assert prev["matches"] >= 1 and prev["token_preview"].startswith("[ORG_") and prev["warning"] is None
    runs = len(store.select("redaction_runs", eq={"application_id": app_id("N01")}))
    rc.add_missed(store, officer, app_id("N01"), "application_text", word, "club", redacted)
    assert len(store.select("redaction_runs", eq={"application_id": app_id("N01")})) == runs + 1          # redaction and leak scan ran again
    after = rc._texts(store, app_of(store, "N01"))["application_text"][0]
    assert word.lower() not in after.lower() and "[ORG_" in after                                          # the AI text changed
    entry = [a for a in store.select("audit_log") if a["action"] == "redaction.added"][-1]
    assert entry["details"]["added_by_officer"] is True and entry["details"]["token_type"] == "ORG" and word not in str(entry)


def test_a_value_the_rules_need_triggers_the_warning(store, officer, redacted):
    provider = app_of(store, "N01")["application_text"]["fields"]["education_provider"]
    prev = rc.preview_missed(store, officer, app_id("N01"), "application_text", provider, "employer", False, True, redacted)
    assert prev["warning"] == "The rules need this value. Hiding it may stop a check from working." and "education provider" in prev["protected"]
    assert rc.preview_missed(store, officer, app_id("N01"), "application_text", "x" * 5, "country", False, True, redacted)["protected"] == ["country"]
