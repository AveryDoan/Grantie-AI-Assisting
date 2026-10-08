"""Step 1: configuration and recognisers (synthetic data only)."""

from __future__ import annotations

import pytest

pytest.importorskip("presidio_analyzer")
pytest.importorskip("en_core_web_lg")

from redaction.config import load_config  # noqa: E402
from redaction.detector import Detector  # noqa: E402
from redaction.recognizers import KnownValue, known_value_patterns  # noqa: E402
from tests.redaction.names import NAMES, TEMPLATES  # noqa: E402


@pytest.fixture(scope="module")
def detector() -> Detector:
    return Detector()


def found(detector: Detector, text: str, known=None, kind="free_text") -> dict[str, str]:
    """Detected text -> token type."""
    return {text[d.start : d.end]: d.token_type for d in detector.detect(text, known, kind)}


# ---------------------------------------------------------------- config


def test_config_loads_and_hash_is_stable():
    a, b = load_config(), load_config()
    assert a.config_hash == b.config_hash and len(a.config_hash) == 64
    assert a.token_type("PERSON") == "PERSON" and a.field_token_type("date_of_birth") == "DOB"
    assert a.format_token("PERSON", 3) == "[PERSON_3]"


def test_config_rejects_bad_regex(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(open(load_config.__globals__["DEFAULT_PATH"]).read().replace("score: 0.4", "score: 0.4\n    extra: 1", 1)
                   .replace("'\\b[0-9]{13}\\b'", "'([unclosed'"))
    with pytest.raises(Exception):
        load_config(bad)


# ---------------------------------------------------------------- phones


@pytest.mark.parametrize(
    "phone",
    ["0412 345 678", "0412345678", "(08) 8946 6666", "+61 412 345 678", "+84 912 345 678", "+44 20 7946 0958",
     "+1 (415) 555-0134", "+91 98765 43210", "+977 980-1234567", "+63 917 123 4567"],
)
def test_phone_numbers_au_and_international(detector, phone):
    text = f"You can call me on {phone} any time."
    assert found(detector, text).get(phone) == "PHONE", found(detector, text)


# ---------------------------------------------------------------- postcodes / addresses


def test_au_postcode_in_address_context(detector):
    text = "My postal address is 8 Example Street, Darwin NT 0800, Australia."
    f = found(detector, text)
    assert "NT 0800" in f and f["NT 0800"] == "ADDRESS"
    assert any(v == "ADDRESS" and "Example Street" in k for k, v in f.items())
    assert "Darwin" not in f and "Australia" not in f  # NT place and country are kept


def test_years_and_rule_dates_are_never_postcodes(detector):
    text = "The course starts on 5 October 2026 and I arrive on 20 September 2026. Referee letter dated 2025."
    assert found(detector, text) == {}


# ---------------------------------------------------------------- IDs


@pytest.mark.parametrize(
    "text, value",
    [
        ("My passport number is N1234567.", "N1234567"),
        ("Passport: PA9876543", "PA9876543"),
        ("Visa grant number 0123456789012 was issued.", "0123456789012"),
        ("Transaction reference TRN: EGO1234567", "TRN: EGO1234567"),
        ("My student ID is s1234567.", "s1234567"),
        ("CoE code E0123456 attached.", "E0123456"),
    ],
)
def test_id_patterns(detector, text, value):
    assert found(detector, text).get(value) == "ID", found(detector, text)


def test_abn_is_redacted(detector):
    assert found(detector, "My ABN is 51 824 753 556.").get("51 824 753 556") == "ID"


# ---------------------------------------------------------------- email / URLs / handles


def test_emails_including_unusual_domains(detector):
    f = found(detector, "Email me at mai.tran@example.invalid or m.tran@uni.example.edu.vn please.")
    assert f.get("mai.tran@example.invalid") == "EMAIL" and f.get("m.tran@uni.example.edu.vn") == "EMAIL"


def test_social_urls_and_handles_but_not_provider_websites(detector):
    text = "See linkedin.com/in/mai-tran-77 and instagram.com/maitran.rn or @maitran_rn. Course info: www.cdu.edu.au/nursing."
    f = found(detector, text)
    assert f.get("linkedin.com/in/mai-tran-77") == "URL"
    assert f.get("instagram.com/maitran.rn") == "URL"
    assert f.get("@maitran_rn") == "HANDLE"
    assert not any("cdu.edu.au" in k for k in f)  # a provider website identifies no one


# ---------------------------------------------------------------- dates of birth


def test_only_date_of_birth_is_redacted(detector):
    text = "Date of birth: 12/03/2007\nCourse start date: 05/10/2026\nI was born on 12 March 2007 in Hanoi."
    f = found(detector, text)
    assert f.get("12/03/2007") == "DOB" and f.get("12 March 2007") == "DOB"
    assert "05/10/2026" not in f  # rule-relevant date kept, format untouched


# ---------------------------------------------------------------- known values


def test_known_values_caught_in_any_form(detector):
    known = [
        KnownValue("Nguyễn Văn An", "PERSON", "applicant_name"),
        KnownValue("an.nguyen@example.invalid", "EMAIL", "email"),
        KnownValue("+84 912 345 678", "PHONE", "phone"),
        KnownValue("2007-03-12", "DOB", "date_of_birth"),
        KnownValue("N1234567", "ID", "passport_number"),
    ]
    text = ("NGUYEN VAN AN signed the declaration. Contact: An.Nguyen@example.invalid, 0912 345 678. "
            "Born 12/03/2007. Passport N 123 4567.")
    f = found(detector, text, known)
    assert f.get("NGUYEN VAN AN") == "PERSON"  # case and accents differ
    assert f.get("An.Nguyen@example.invalid") == "EMAIL"
    assert f.get("0912 345 678") == "PHONE"  # national format of a +84 number
    assert f.get("12/03/2007") == "DOB"
    assert f.get("N 123 4567") == "ID"  # spacing differs


def test_reversed_name_order_and_parts(detector):
    known = [KnownValue("Linh Tran", "PERSON", "applicant_name")]
    f = found(detector, "Visa Holder: TRAN, Linh. Linh is hard-working.", known)
    assert f.get("TRAN") == "PERSON" and f.get("Linh") == "PERSON"


def test_name_particles_are_not_redacted_alone():
    pats = [p for p, _ in known_value_patterns(KnownValue("Ahmed bin Rashid", "PERSON", "applicant_name"))]
    assert not any(p.endswith("bin(?!\\w)") for p in pats)


def test_known_value_overrides_allowlist(detector):
    """An applicant named Katherine is still redacted, though Katherine is an NT town."""
    f = found(detector, "Katherine Smith lives in Hanoi.", [KnownValue("Katherine Smith", "PERSON", "applicant_name")])
    assert f.get("Katherine Smith") == "PERSON"


def test_referee_letter_people_become_referee_tokens(detector):
    f = found(detector, "I have supervised the applicant for two years. Yours sincerely, Dr Hamid Karimi", kind="referee_letter")
    assert "REFEREE" in f.values() and "PERSON" not in f.values()


# ---------------------------------------------------------------- allowlist / keep-list


def test_allowlisted_providers_courses_and_terms_are_kept(detector):
    text = ("I applied to Charles Darwin University, Darwin City College and Alice Springs College of Australia "
            "for a Bachelor of Nursing on a subclass 500 visa, and I hold the CDU Global Merit Scholarship.")
    assert found(detector, text) == {}


def test_countries_and_nationality_are_kept(detector):
    f = found(detector, "I am a citizen of Nepal and currently live in Vietnam, then I will move to Australia.")
    assert not any(k in f for k in ("Nepal", "Vietnam", "Australia"))


def test_denylist_is_always_redacted(tmp_path):
    from redaction.config import DEFAULT_PATH

    cfg_text = open(DEFAULT_PATH).read().replace("denylist: []", 'denylist:\n  - {value: "Kiwi Pharma", type: PERSON}')
    p = tmp_path / "deny.yaml"
    p.write_text(cfg_text)
    det = Detector(load_config(p))
    text = "I interned at Kiwi Pharma last summer."
    assert {text[d.start : d.end] for d in det.detect(text)} == {"Kiwi Pharma"}


# ---------------------------------------------------------------- fairness: names from many cultures


def _covered(detector, text: str, name: str) -> bool:
    start = text.index(name)
    spans = [(d.start, d.end) for d in detector.detect(text) if d.token_type in ("PERSON", "REFEREE")]
    # every character of the name must be inside some detection
    return all(any(s <= i < e for s, e in spans) for i in range(start, start + len(name)) if not name[i - start].isspace())


def test_multi_culture_name_recall_report(detector, capsys):
    """Detector-only recall (no known values). Reported honestly; the known-value
    recogniser is the safety net for the applicant's own and referees' names."""
    rows, hits, total = [], 0, 0
    for group, names in NAMES.items():
        g_hits = g_total = 0
        for name in names:
            for tpl in TEMPLATES:
                text = tpl.format(name=name)
                ok = _covered(detector, text, name)
                g_hits += ok
                g_total += 1
        rows.append((group, g_hits, g_total))
        hits += g_hits
        total += g_total
    with capsys.disabled():
        print(f"\nDetector-only name recall: {hits}/{total} = {hits / total:.0%}")
        for group, h, t in rows:
            print(f"  {group:36} {h}/{t}")
    assert hits / total >= 0.75  # floor, not a target: misses are listed above and in the eval report


def test_known_values_give_full_recall_for_every_culture(detector):
    for names in NAMES.values():
        for name in names:
            for tpl in TEMPLATES:
                text = tpl.format(name=name)
                start = text.index(name)
                dets = detector.detect(text, [KnownValue(name, "PERSON", "applicant_name")])
                spans = [(d.start, d.end) for d in dets]
                assert all(any(s <= i < e for s, e in spans) for i in range(start, start + len(name)) if not text[i].isspace()), name
