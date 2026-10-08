"""Study NT rule pack v2 (S1-S16, D1-D7, M1-M5)."""

import json

from app.llm import LLMClient
from app.llm.stub import OfflineStubProvider
from app.pipeline.orchestrator import assess_application, load_reference_lists
from app.pipeline.rules_loader import load_rule_pack
from eval.harness import run_evaluation
from seed import data
from seed.data import sid
from tests.conftest import ScriptedProvider, app_id

SOPL_DEMO = ["Registered Nurse (Aged Care)", "Registered Nurse (Medical)", "Software Engineer", "Chef"]


def _assess(store, settings, code, llm, lists=None):
    app = store.select("applications", eq={"id": app_id(code)})[0]
    pack = load_rule_pack(store, app["rule_pack_id"])
    out = assess_application(
        application=app, documents=store.select("documents", eq={"application_id": app["id"]}), pack=pack,
        register_rows=store.select("mock_grants_register", eq={"applicant_id": app["applicant_id"]}), llm=llm,
        settings=settings, applicant=store.select("applicants", eq={"id": app["applicant_id"]})[0],
        reference_lists=lists if lists is not None else load_reference_lists(store, pack),
    )
    return {f.rule_code: f for f in out.findings}, out


def test_every_code_checked_rule_matches_the_answer_key(store, stub_llm, settings):
    """Regression guard: deterministic rules must agree with the human answer key on every case."""
    report = run_evaluation(store, stub_llm, settings, provider_label="offline stub", write_db=False)
    rules = {r["id"]: r for r in store.select("rules")}
    wrong = [(r.case_code, r.rule_code, r.expected, r.predicted) for r in report.results
             if rules[r.rule_id]["check_method"] == "code" and not r.correct]
    assert wrong == []
    assert report.injection and all(t["flagged"] and not t["obeyed"] for t in report.injection)


def test_provider_list_lookup_and_unloaded_occupation_list(store, stub_llm, settings):
    f, _ = _assess(store, settings, "N01", stub_llm)
    assert f["S1"].ai_status == "Met"  # Charles Darwin University is on the supplied provider list
    assert f["S3"].ai_status == "Unclear" and f["S3"].error_flag and "not been loaded" in f["S3"].rationale
    assert f["S3"].ai_status != "Met"


def test_occupation_list_matches_course_by_word_stem(store, stub_llm, settings):
    lists = {"nt_education_providers": data.REFERENCE_LISTS[0]["items"], "nt_skilled_occupation_priority_list": SOPL_DEMO}
    f, _ = _assess(store, settings, "N01", stub_llm, lists)
    assert f["S3"].ai_status == "Met" and "Registered Nurse" in f["S3"].rationale  # Bachelor of Nursing -> Nurse
    lists["nt_skilled_occupation_priority_list"] = ["Chef", "Electrician"]
    f, _ = _assess(store, settings, "N01", stub_llm, lists)
    assert f["S3"].ai_status == "Unclear" and not f["S3"].error_flag  # no clear link: the officer decides


def test_course_not_on_provider_list_is_unclear_not_not_met(store, stub_llm, settings):
    lists = {"nt_education_providers": ["Some Other College"], "nt_skilled_occupation_priority_list": SOPL_DEMO}
    f, _ = _assess(store, settings, "N01", stub_llm, lists)
    assert f["S1"].ai_status == "Unclear"


def test_under_18_is_flagged_not_failed(store, stub_llm, settings):
    f, _ = _assess(store, settings, "N04", stub_llm)
    assert f["S15"].ai_status == "Unclear" and "to be confirmed" in f["S15"].rationale


def test_flight_screenshot_is_not_evidence_of_arrival(store, stub_llm, settings):
    f, _ = _assess(store, settings, "N02", stub_llm)
    assert f["D2"].ai_status == "Needs evidence" and "screenshot" in f["D2"].rationale


def test_referee_letters_are_redacted_before_the_llm_and_quotes_verified(store, settings):
    seen = []

    def respond(prompt, schema):
        seen.append(prompt)
        return OfflineStubProvider().generate(prompt, schema, temperature=0, timeout=1)

    llm = LLMClient(ScriptedProvider(respond), temperature=0.0)
    f, out = _assess(store, settings, "N01", llm)
    referee_prompts = [p for p in seen if p.task == "referee"]
    assert len(referee_prompts) == 2
    for p in referee_prompts:
        assert "Linh" not in p.user and "Hoa Pham" not in p.user and "Minh Le" not in p.user  # names redacted
        assert "<document_data>" in p.user
    assert f["D4"].ai_status == "Met" and "check the signature and letterhead by eye" in f["D4"].rationale
    assert f["M2"].ai_status == "Evidence only" and f["M2"].supporting_quotes
    assert all(q["verified"] for q in f["M2"].supporting_quotes)  # verified against each letter, not the form


def test_fabricated_referee_quote_makes_m2_invalid(store, settings):
    def respond(prompt, schema):
        if prompt.task == "referee":
            out = json.loads(OfflineStubProvider().generate(prompt, schema, temperature=0, timeout=1))
            out["highlights"] = ["This student is the best applicant I have ever taught in forty years."]
            return json.dumps(out)
        return OfflineStubProvider().generate(prompt, schema, temperature=0, timeout=1)

    f, _ = _assess(store, settings, "N01", LLMClient(ScriptedProvider(respond), temperature=0.0))
    assert f["M2"].is_valid is False


def test_merit_criteria_never_get_an_ai_status(store, stub_llm, settings):
    f, _ = _assess(store, settings, "N01", stub_llm)
    for code in ("M1", "M2", "M3", "M4"):
        assert f[code].ai_status == "Evidence only" and f[code].confidence is None


def test_referee_extraction_failure_is_unclear_never_met(store, settings):
    from app.llm import LLMError

    def respond(prompt, schema):
        if prompt.task == "referee":
            return LLMError("down")
        return OfflineStubProvider().generate(prompt, schema, temperature=0, timeout=1)

    f, _ = _assess(store, settings, "N01", LLMClient(ScriptedProvider(respond), temperature=0.0))
    assert f["D4"].ai_status == "Unclear" and f["D4"].error_flag
    assert f["D5"].ai_status == "Unclear" and f["D5"].error_flag


def test_loading_a_reference_list_changes_the_input_hash(store, officer, stub_llm, settings):
    from app.pipeline.orchestrator import run_assessment

    first = run_assessment(store, officer, app_id("N01"), stub_llm, settings)
    store.update("reference_lists", {"items": SOPL_DEMO}, eq={"name": "nt_skilled_occupation_priority_list"})
    second = run_assessment(store, officer, app_id("N01"), stub_llm, settings)
    assert second["id"] != first["id"] and second["reused"] is False


def test_seed_retires_older_pack_versions(store):
    old = store.insert("rule_packs", {"grant_program_id": data.SNT_PROGRAM, "version": "v0", "status": "approved"})[0]
    from seed.run import seed

    seed(store)
    assert store.select("rule_packs", eq={"id": old["id"]})[0]["status"] == "retired"
    assert store.select("rule_packs", eq={"id": sid("pack:snt:v2")})[0]["status"] == "approved"
