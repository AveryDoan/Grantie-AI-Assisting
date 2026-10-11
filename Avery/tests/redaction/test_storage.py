"""Step 5: encrypted token-map storage and restore() (synthetic data only)."""

from __future__ import annotations

import copy
import json

import pytest

pytest.importorskip("presidio_analyzer")
pytest.importorskip("en_core_web_lg")

from app.store.memory import MemoryStore  # noqa: E402
from redaction.crypto import DecryptionFailed, RedactionKeyMissing, TokenCipher, generate_key  # noqa: E402
from redaction.detector import Detector  # noqa: E402
from redaction.documents import document_source  # noqa: E402
from redaction.extract import extract_document  # noqa: E402
from redaction.pipeline import run_redaction  # noqa: E402
from redaction.restore import restore  # noqa: E402
from redaction.storage import TABLE, load_token_map, save_token_map  # noqa: E402
from redaction.structured import APPLICATION_SOURCE  # noqa: E402
from seed import data  # noqa: E402
from tests.redaction.pdfs import make_pdf  # noqa: E402

KEY = generate_key()
CASES = {c["code"]: c for c in data.CASES if c["code"].startswith("N")}
LETTER = ["To whom it may concern,", "I have known Linh Tran for 3 years as her teacher. TRAN, Linh is reliable.",
          "Referee name: Ms Hoa Pham", "Position: Head of Science", "Date: 12 May 2026"]


@pytest.fixture(scope="module")
def detector() -> Detector:
    return Detector()


def outcome_with_letter(detector, code="N01"):
    doc = extract_document("doc-1", "ref.pdf", make_pdf([LETTER]))
    return run_redaction(f"app-{code}", CASES[code]["application_text"], [doc],
                         document_kinds={"doc-1": "referee_letter"}, detector=detector)


# ---------------------------------------------------------------- encryption


def test_encrypt_round_trip_with_fresh_nonce():
    c = TokenCipher(KEY)
    a, b = c.encrypt("app-1", "[PERSON_1]", "Linh Tran"), c.encrypt("app-1", "[PERSON_1]", "Linh Tran")
    assert a != b and a.startswith("v1:") and "Linh" not in a
    assert c.decrypt("app-1", "[PERSON_1]", a) == "Linh Tran"


def test_ciphertext_is_bound_to_its_application_and_token():
    c = TokenCipher(KEY)
    blob = c.encrypt("app-1", "[PERSON_1]", "Linh Tran")
    with pytest.raises(DecryptionFailed):
        c.decrypt("app-2", "[PERSON_1]", blob)   # copied to another application
    with pytest.raises(DecryptionFailed):
        c.decrypt("app-1", "[PERSON_2]", blob)   # moved to another token
    tampered = blob[:-4] + ("AAAA" if not blob.endswith("AAAA") else "BBBB")
    with pytest.raises(DecryptionFailed):
        c.decrypt("app-1", "[PERSON_1]", tampered)


def test_no_key_means_nothing_is_stored(monkeypatch, detector):
    monkeypatch.delenv("REDACTION_KEY", raising=False)
    with pytest.raises(RedactionKeyMissing):
        TokenCipher()


def test_key_rotation_keeps_old_rows_readable():
    old, new = generate_key(), generate_key()
    blob = TokenCipher(old).encrypt("app-1", "[EMAIL_1]", "x@example.invalid")
    assert TokenCipher(new, old).decrypt("app-1", "[EMAIL_1]", blob) == "x@example.invalid"
    with pytest.raises(DecryptionFailed):
        TokenCipher(new).decrypt("app-1", "[EMAIL_1]", blob)


def test_decryption_errors_never_contain_plaintext():
    c = TokenCipher(KEY)
    blob = c.encrypt("app-1", "[PERSON_1]", "Linh Tran")
    with pytest.raises(DecryptionFailed) as exc:
        c.decrypt("app-9", "[PERSON_1]", blob)
    assert "Linh" not in str(exc.value)


# ---------------------------------------------------------------- storage


def test_stored_rows_contain_no_plaintext(detector):
    out = outcome_with_letter(detector)
    store = MemoryStore()
    n = save_token_map(store, out.application_id, None, out.token_map, TokenCipher(KEY))
    rows = store.select(TABLE)
    assert n == len(rows) > 0
    dumped = json.dumps(rows)
    fields = CASES["N01"]["application_text"]["fields"]
    for value in ("Linh", "Tran", fields["email"], fields["phone"], fields["date_of_birth"], "Fictional Lane", "Hoa Pham"):
        assert value not in dumped, value
    assert {r["entity_type"] for r in rows} >= {"PERSON", "EMAIL", "PHONE", "DOB", "ADDRESS", "REFEREE"}


def test_save_is_idempotent_and_replaces_previous_rows(detector):
    out = outcome_with_letter(detector)
    store = MemoryStore()
    c = TokenCipher(KEY)
    first = save_token_map(store, out.application_id, None, out.token_map, c)
    second = save_token_map(store, out.application_id, None, out.token_map, c)
    assert first == second == len(store.select(TABLE))


def test_round_trip_keeps_tokens_types_and_canonical_forms(detector):
    out = outcome_with_letter(detector)
    store, c = MemoryStore(), TokenCipher(KEY)
    save_token_map(store, out.application_id, None, out.token_map, c)
    loaded = load_token_map(store, out.application_id, c)
    assert {t: (e.token_type, e.canonical) for t, e in loaded.entries.items()} == \
           {t: (e.token_type, e.canonical) for t, e in out.token_map.entries.items()}


# ---------------------------------------------------------------- 9. restore() reproduces the original exactly


@pytest.mark.parametrize("code", ["N01", "N08", "N09", "N07"])
def test_restore_reproduces_application_text_exactly_after_storage(detector, code):
    out = run_redaction(f"app-{code}", CASES[code]["application_text"], detector=detector)
    store, c = MemoryStore(), TokenCipher(KEY)
    save_token_map(store, out.application_id, None, out.token_map, c)
    loaded = load_token_map(store, out.application_id, c)
    from redaction.structured import FieldRedactor

    original = FieldRedactor(detector.cfg, detector).redact(CASES[code]["application_text"]).original_text
    assert restore(out.redacted_text, loaded, APPLICATION_SOURCE) == original


def test_restore_reproduces_documents_exactly_including_name_variants(detector):
    out = outcome_with_letter(detector)
    store, c = MemoryStore(), TokenCipher(KEY)
    save_token_map(store, out.application_id, None, out.token_map, c)
    loaded = load_token_map(store, out.application_id, c)
    doc = next(d for d in out.documents if d.document_id == "doc-1")
    original = extract_document("doc-1", "ref.pdf", make_pdf([LETTER])).text
    assert "[PERSON_1]" in doc.redacted_text and "TRAN, Linh" not in doc.redacted_text
    assert restore(doc.redacted_text, loaded, document_source("doc-1")) == original  # "Linh Tran" and "TRAN, Linh" both back


def test_restore_without_source_uses_canonical_and_never_guesses(detector):
    app = copy.deepcopy(CASES["N01"]["application_text"])
    app["fields"]["postal_address"] = "77 Other Road, Da Nang, Vietnam"  # a second, different address, same class
    out = run_redaction("app-x", app, detector=detector)
    text = "The applicant [PERSON_1] can be reached at [EMAIL_1]; address [LOCATION: outside Australia]."
    shown = restore(text, out.token_map)
    assert shown.startswith("The applicant Linh Tran can be reached at linh.tran@example.invalid")
    assert "[LOCATION: outside Australia]" in shown  # two different addresses: placeholder kept, not guessed
    assert restore("Unknown [PERSON_9] stays", out.token_map) == "Unknown [PERSON_9] stays"
