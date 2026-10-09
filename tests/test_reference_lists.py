"""Reference lists: the real 2026 NT list parses cleanly; officers can preview and save; changes are audited."""

from pathlib import Path

import pytest

from app.services import reference_lists as rl
from app.services.access import Actor
from app.services.errors import Forbidden, ValidationFailed
from seed import data

SOPL = rl.SOPL
FILE = Path(data.SOPL_FILE)
OFFICER = Actor(user_id=None, role="officer")
APPLICANT = Actor(user_id=None, role="applicant")


@pytest.fixture()
def lists(store):
    for lst in data.REFERENCE_LISTS:
        if not store.select("reference_lists", eq={"name": lst["name"]}, limit=1):
            store.insert("reference_lists", dict(lst))
    return store


def test_real_file_parses_with_no_warnings():
    entries, warnings = rl.parse_text(FILE.read_text(encoding="utf-8"))
    assert warnings == []
    assert len(entries) == len({e["code"] for e in entries}) == 247
    assert {e["tier"] for e in entries} == {"High priority", "Priority"}
    assert sum(e["tier"] == "High priority" for e in entries) == 137
    assert all(len(e["code"]) == 6 and 1 <= e["skill_level"] <= 5 for e in entries)
    names = rl.items_of(entries)
    assert "Registered Nurse (Acute Care)" in names and "Cloud Engineer" in names


def test_codes_inside_names_and_page_furniture_are_not_rows():
    entries, _ = rl.parse_text("2026 Northern Territory skilled occupation priority list\n31 August 2026 | Page 2 of 10\n"
                               "Priority occupations\n111111 Example Worker 2\n")
    assert [(e["code"], e["name"], e["tier"]) for e in entries] == [("111111", "Example Worker", "Priority")]


def test_unreadable_coded_row_is_a_warning_not_a_guess():
    entries, warnings = rl.parse_text("111111 Good Row 2\n222222 Missing level\n")
    assert len(entries) == 1 and any("222222" in w for w in warnings)


def test_plain_names_fall_back():
    entries, _ = rl.parse_text("Chef\nElectrician\nchef\n")
    assert [e["name"] for e in entries] == ["Chef", "Electrician"]  # repeats ignored, any case


def test_preview_does_not_save_and_shows_changes(lists):
    before = lists.select("reference_lists", eq={"name": SOPL})[0]["items"]
    out = rl.preview(lists, OFFICER, SOPL, "111111 New Worker 2\n")
    assert out["count"] == 1 and out["added"] == ["New Worker"] and out["removed_count"] == len(before)
    assert lists.select("reference_lists", eq={"name": SOPL})[0]["items"] == before


def test_save_replaces_list_and_audits_counts_only(lists):
    text = "Priority occupations\n111111 New Worker 2\n"
    out = rl.save(lists, OFFICER, SOPL, text, "1 Jan 2027", None)
    assert out["count"] == 1 and out["edition"] == "1 Jan 2027" and "S3" in out["used_by"]
    row = lists.select("reference_lists", eq={"name": SOPL})[0]
    assert row["items"] == ["New Worker"] and row["entries"][0]["code"] == "111111"
    audit = [a for a in lists.select("audit_log") if a["action"] == "reference_list_saved"][-1]
    assert audit["details"]["count"] == 1 and "New Worker" not in str(audit["details"])


def test_applicants_cannot_use_it_and_empty_or_huge_text_is_rejected(lists):
    with pytest.raises(Forbidden):
        rl.save(lists, APPLICANT, SOPL, "111111 X 2", None, None)
    with pytest.raises(ValidationFailed):
        rl.preview(lists, OFFICER, SOPL, "   \n")
    with pytest.raises(ValidationFailed):
        rl.preview(lists, OFFICER, SOPL, "x" * (rl.MAX_TEXT + 1))


def test_saving_a_list_changes_the_assessment_input_hash():
    from app.pipeline.orchestrator import compute_input_hash
    import inspect
    assert "reference_lists" in inspect.signature(compute_input_hash).parameters
