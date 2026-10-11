"""Check 4 (cross_application): linking applications by keyed hashes, never by stored identifiers."""

import json
import re

from app.pipeline.consistency import cross_application as ca
from app.pipeline.orchestrator import run_assessment
from app.services import consistency as service
from tests.consistency.conftest import flags_of, make_pool, raised, stub

RING = ("F07", "F08", "F09")
KEY = "k" * 40


def test_the_same_phone_written_two_ways_gives_the_same_hash():
    a, b = ca.norm_phone("+61 7 5550 3344"), ca.norm_phone("(07) 5550 3344")
    assert a == b == "755503344"
    assert ca.keyed_hash(KEY, "referee_phone", a) == ca.keyed_hash(KEY, "referee_phone", b)
    assert ca.norm_phone("12") is None


def test_normalisers():
    assert ca.norm_email("  A.B@Example.ORG ") == "a.b@example.org"
    assert ca.norm_name("Dr Maya LINDQVIST") == ca.norm_name("lindqvist, Maya") == "lindqvist maya"
    assert ca.norm_name("Madonna") is None                      # one word is not enough to link anyone
    assert ca.norm_address("9 Marigold Court, Pune 411001, India") == ca.norm_address("9  marigold court,pune 411001 india")


def test_the_hash_depends_on_the_key_and_the_kind_and_reveals_nothing():
    h = ca.keyed_hash(KEY, "contact_phone", "491570313")
    assert re.fullmatch(r"[0-9a-f]{64}", h) and "491570313" not in h
    assert h != ca.keyed_hash("other-key" * 5, "contact_phone", "491570313")
    assert h != ca.keyed_hash(KEY, "referee_phone", "491570313")


LETTER = ("I am writing to support {n}'s application. {n} has been a dedicated member of our programme and consistently shows diligence in "
          "everything she does. In class she asks thoughtful questions, helps classmates who are struggling and finishes every project on time. "
          "Colleagues describe {n} as generous and dependable, and she has represented the institute at two regional events.")


def test_a_letter_reused_with_other_names_is_similar_but_a_different_letter_is_not():
    red = lambda n: LETTER.format(n=n)   # noqa: E731
    a, b = ca.shingle_hashes(red("[REFEREE_1]"), KEY), ca.shingle_hashes(red("[REFEREE_2]"), KEY)
    assert ca.jaccard(ca.sketch(a), ca.sketch(b)) > 0.95                 # placeholders count by type, so renamed copies match
    other = ("Tomasz leads our robotics club with real patience. He tests every idea twice, shares credit with his team and has built "
             "the club into a group that other schools invite to demonstrations. I am glad to recommend him to the panel.")
    assert ca.jaccard(ca.sketch(a), ca.sketch(ca.shingle_hashes(other, KEY))) < 0.1


def test_form_like_label_lines_do_not_make_unrelated_letters_look_alike():
    a = "Referee name: [REFEREE_1]\nPosition: Teacher\nDate: 3 June 2026\nI taught her chemistry for three years and admire her curiosity and care."
    b = "Referee name: [REFEREE_2]\nPosition: Teacher\nDate: 9 July 2026\nHe coaches our debating team and has led it to two regional finals."
    assert ca.prose(a).strip() == "I taught her chemistry for three years and admire her curiosity and care."
    assert "Referee name" not in ca.prose(b) and "Position" not in ca.prose(b)


# ---------------------------------------------------------------- the pool (F07 to F09 are the linked applications)


def test_the_three_linked_applications_each_raise_the_expected_flags(pool):
    for code in RING:
        got = raised(pool, code)
        assert {"cross_application.shared_referee_phone", "cross_application.shared_referee_email_domain"} <= got, code
        assert got & {"cross_application.reused_wording", "cross_application.identical_text"}, code
    assert "cross_application.shared_contact_phone" in raised(pool, "F07") | raised(pool, "F09")
    assert "cross_application.shared_contact_phone" not in raised(pool, "F08")


def test_every_application_outside_the_ring_has_no_cross_application_flag(pool):
    for code in ("F01", "F02", "F03", "F04", "F05", "F06", "F10", "N08"):
        assert not [i for i in raised(pool, code) if i.startswith("cross_application")], code


def test_a_shared_email_domain_is_only_a_weak_signal_and_a_shared_phone_is_strong(pool):
    by = {f["check_id"]: f["strength"] for f in flags_of(pool, "F07")}
    assert by["cross_application.shared_referee_email_domain"] == "weak" and by["cross_application.shared_referee_phone"] == "strong"


def test_flags_name_the_linked_applications_by_reference_and_never_a_value(pool):
    for code in RING:
        for f in flags_of(pool, code):
            for e in f["evidence"]:
                assert e["kind"] == "link" and e["source"].startswith("application:")
                assert re.search(r"APP-[0-9A-F]{6}", e["label"])
                assert "5550" not in e["label"] and "riverbend" not in e["label"].lower() and "@" not in e["label"]


def test_the_stored_hashes_are_hashes_and_no_identifier_is_stored_anywhere(pool):
    store = pool["store"]
    rows = store.select("identifier_hashes")
    assert rows and all(re.fullmatch(r"[0-9a-f]{64}", r["hash"]) for r in rows)
    # Timestamps and random row ids are dropped: ".155502" or "85550feb-..." can contain "5550" by chance.
    tables = [[{k: v for k, v in r.items() if not k.endswith("_at") and k != "id"} for r in store.select(t)] for t in ("identifier_hashes", "document_fingerprints", "consistency_flags")]
    dump = json.dumps(tables, default=str).lower()
    for raw in ("5550 3344", "5550", "riverbend-institute", "d.crossley", "m.lindqvist", "s.osei", "crossley", "lindqvist", "0491 570 313", "491570313",
                "dhruv.ashcombe", "prayag marg", "pokhara 33700"):
        assert raw not in dump, raw
    assert "sketch" in dump and not any(re.search(r"\b(shingle|word)\b.*[a-z]{12,}", json.dumps(r["sketch"])) for r in store.select("document_fingerprints"))


def test_linked_groups_name_the_attribute_not_the_value(pool):
    groups = service.linked_groups(pool["store"], pool["actor"], pool["settings"])
    assert len(groups) == 1 and len(groups[0]["applications"]) == 3
    labels = {a["label"] for a in groups[0]["attributes"]}
    assert {"Shared referee phone number", "Shared referee email domain", "Shared contact phone number"} <= labels
    assert "5550" not in json.dumps(groups) and "riverbend" not in json.dumps(groups).lower()
    assert {a["reference"] for a in groups[0]["applications"]} == {f"APP-{pool['ids'][c].replace('-', '')[:6].upper()}" for c in RING}


def test_no_links_without_a_hash_key_and_nothing_is_stored():
    store, ids, actor, settings = make_pool()
    settings = settings.model_copy(update={"identifier_hash_key": type(settings.identifier_hash_key)("")})
    for code in RING:
        run_assessment(store, actor, ids[code], stub(), settings)
    assert store.select("identifier_hashes") == [] and store.select("document_fingerprints") == []
    run = store.select("assessment_runs", eq={"application_id": ids["F07"]})[0]
    assert "IDENTIFIER_HASH_KEY" in run["consistency_trace"]["cross_application"]["skipped"]
    assert not [f for f in store.select("consistency_flags") if f["check_type"] == "cross_application"]


def test_the_same_applicant_and_other_organisations_are_never_linked():
    store, ids, actor, settings = make_pool()
    for code in RING:
        run_assessment(store, actor, ids[code], stub(), settings)
    assert ca.linked_apps(store, ids["F07"]) == {ids["F08"], ids["F09"]}
    # One person with two applications is not "unrelated applicants".
    store.update("applications", {"applicant_id": store.select("applications", eq={"id": ids["F07"]})[0]["applicant_id"]}, eq={"id": ids["F08"]})
    assert ids["F08"] not in ca._peer_apps(store, ids["F07"])
    # An application in another organisation's program is not visible.
    from seed import data

    org_b = store.insert("organisations", {"name": "Org B"})[0]["id"]
    prog_b = store.insert("grant_programs", {"organisation_id": org_b, "name": "Other program"})[0]["id"]
    store.update("applications", {"grant_program_id": prog_b}, eq={"id": ids["F09"]})
    assert ids["F09"] not in ca._peer_apps(store, ids["F07"])
    assert ca.linked_apps(store, ids["F07"]) == set()        # nothing left that this officer's organisation may see
    assert data.ORG_ID != org_b


def test_reassessing_keeps_the_same_flags_instead_of_adding_more(pool):
    store, ids, actor, settings = make_pool()
    for code in RING:
        run_assessment(store, actor, ids[code], stub(), settings)
    before = sorted(f["flag_key"] for f in store.select("consistency_flags", eq={"application_id": ids["F07"]}))
    run_assessment(store, actor, ids["F07"], stub(), settings, force=True)
    assert sorted(f["flag_key"] for f in store.select("consistency_flags", eq={"application_id": ids["F07"]})) == before
