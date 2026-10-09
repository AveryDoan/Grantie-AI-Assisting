"""Local redaction sandbox and passport layer. FAKE passport text only."""

from __future__ import annotations

import json
import logging
import socket

import pytest

pytest.importorskip("presidio_analyzer")
pytest.importorskip("en_core_web_lg")

from redaction import sandbox  # noqa: E402
from redaction.crypto import generate_key  # noqa: E402
from redaction.passport import parse_passport  # noqa: E402
from tests.redaction import fake_passport as fp  # noqa: E402
from tests.redaction.pdfs import PNG_1PX, make_pdf  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REDACTION_KEY", generate_key())
    monkeypatch.setenv("GRANTIE_SANDBOX_DIR", str(tmp_path / "sbx"))
    (tmp_path / "sbx" / "input").mkdir(parents=True)
    src = tmp_path / "sbx" / "input" / "Mira Vellamore passport.txt"  # a file name with a name in it
    src.write_text(fp.passport_text(), encoding="utf-8")
    return {"src": src, "out_root": tmp_path / "sbx" / "output", "root": tmp_path / "sbx"}


def run(env, *args, **kw):
    lines: list[str] = []
    out = sandbox.run(env["src"], list(args), out_root=env["out_root"], echo=lines.append, **kw)
    return out, "\n".join(lines)


def no_personal(text: str) -> None:
    for v in fp.PERSONAL_VALUES:
        assert v.casefold() not in text.casefold(), v


# ---------------------------------------------------------------- passport layer


def test_mrz_is_found_parsed_and_check_digits_validate():
    p = parse_passport(fp.passport_text())
    assert len(p.mrz_lines) == 2 and p.td3_parsed and all(p.checks_ok.values())
    types = {(k.token_type, k.source) for k in p.known}
    assert {("MRZ", "mrz"), ("PERSON", "mrz"), ("ID", "mrz"), ("DOB", "mrz"), ("PLACE", "label"), ("DOB", "label")} <= types
    assert {"date of expiry", "date of issue", "nationality"} <= set(p.kept_labels)


def test_bilingual_label_does_not_take_the_next_line():
    p = parse_passport(fp.passport_text())
    dob_label = [k.value for k in p.known if k.token_type == "DOB" and k.source == "label"]
    assert dob_label == [fp.DOB_PRINTED]  # not "Sex / Sexe: F" from the next line


# ---------------------------------------------------------------- run


def test_run_writes_all_outputs_and_values_stay_in_the_officer_view(env, caplog):
    caplog.set_level(logging.DEBUG)
    out, stdout = run(env, "country=Utopia", inject_test=False)
    for name in ("1_original_view.html", "2_redacted.txt", "3_report.html", "token_map.enc", "meta.json"):
        assert (out / name).exists(), name
    assert (env["out_root"] / "index.html").exists()
    redacted = (out / "2_redacted.txt").read_text()
    assert "[MRZ_1]" in redacted and "[MRZ_2]" in redacted and "[ID_1]" in redacted and "[DOB_1]" in redacted
    assert "[PLACE_1]" in redacted  # place of birth
    assert "UTOPIAN" in redacted and "Ministry of Foreign Affairs" in redacted  # kept on purpose

    for name in ("2_redacted.txt", "3_report.html", "meta.json", "token_map.enc"):
        no_personal((out / name).read_text())
    no_personal((env["out_root"] / "index.html").read_text())
    no_personal(stdout)
    no_personal("\n".join(r.getMessage() for r in caplog.records))
    assert "vellamore" not in out.name.lower() and env["src"].name not in stdout  # the file name is never used or printed
    assert fp.SURNAME in (out / "1_original_view.html").read_text()  # the officer-only view has the original

    meta = json.loads((out / "meta.json").read_text())
    assert meta["leak_pass"] and meta["mrz"]["td3_parsed"] and meta["location_class"] == "outside_australia"
    assert "Leak scan: PASS" in stdout and "[MRZ_1]" in stdout


def test_report_frames_the_original_instead_of_copying_it(env):
    out, _ = run(env)
    report = (out / "3_report.html").read_text()
    assert 'src="1_original_view.html"' in report
    assert "Needed information kept" in report and "Two-pass consistency" in report and "Location class" in report


def test_inject_test_is_caught_by_the_leak_scan(env):
    out, stdout = run(env, inject_test=True)
    meta = json.loads((out / "meta.json").read_text())
    assert meta["inject_test"]["blocked"] and meta["inject_test"]["token"] == "[ID_1]"
    assert "known_value" in meta["inject_test"]["checks"]
    assert "FAIL, the LLM call would be blocked" in stdout
    no_personal(stdout)
    no_personal((out / "2_redacted.txt").read_text())  # the injected text is never written


def test_restore_rebuilds_the_original_exactly(env):
    out, _ = run(env)
    lines: list[str] = []
    view = sandbox.restore_dir(out, echo=lines.append)
    assert "exactly: yes" in "\n".join(lines)
    import html
    assert fp.SURNAME in view.read_text() and html.escape(fp.mrz()[1]) in view.read_text()
    no_personal("\n".join(lines))


def test_restore_refuses_a_changed_text_and_a_wrong_key(env, monkeypatch):
    out, _ = run(env)
    red = out / "2_redacted.txt"
    red.write_text(red.read_text().replace("Ministry", "Office"))
    with pytest.raises(sandbox.SandboxError) as e:
        sandbox.restore_dir(out, echo=lambda *_: None)
    assert e.value.code == 4
    monkeypatch.setenv("REDACTION_KEY", generate_key())
    with pytest.raises(sandbox.SandboxError, match="could not be decrypted"):
        sandbox.restore_dir(out, echo=lambda *_: None)


# ---------------------------------------------------------------- refusals


def test_missing_key_stops_before_any_output(env, monkeypatch, tmp_path):
    monkeypatch.setenv("REDACTION_KEY", "")
    monkeypatch.setattr(sandbox, "ENV_FILE", tmp_path / "missing.env")
    with pytest.raises(sandbox.SandboxError, match="REDACTION_KEY is not set"):
        run(env)
    assert not env["out_root"].exists()


@pytest.mark.parametrize("name,data", [("photo.png", PNG_1PX), ("scan.pdf", make_pdf([None]))])
def test_images_and_scans_stop_with_the_ocr_message(env, name, data):
    env["src"] = env["src"].with_name(name)
    env["src"].write_bytes(data)
    with pytest.raises(sandbox.SandboxError) as e:
        run(env)
    assert str(e.value).startswith(sandbox.OCR_MESSAGE) and e.value.code == 3
    assert not env["out_root"].exists()


def test_cloud_synced_folders_are_refused(env, tmp_path):
    synced = tmp_path / "Library" / "CloudStorage" / "OneDrive-Example" / "input"
    synced.mkdir(parents=True)
    f = synced / "doc.txt"
    f.write_text(fp.passport_text())
    with pytest.raises(sandbox.SandboxError, match="synced to a cloud service"):
        sandbox.run(f, [], out_root=env["out_root"], echo=lambda *_: None)
    with pytest.raises(sandbox.SandboxError, match="synced to a cloud service"):
        sandbox.run(env["src"], [], out_root=synced.parent / "output", echo=lambda *_: None)


def test_network_is_refused_while_the_sandbox_runs():
    with sandbox.no_network():
        with pytest.raises(OSError, match="disabled"):
            socket.create_connection(("example.com", 80), timeout=1)
        with pytest.raises(OSError):
            socket.getaddrinfo("example.com", 80)
    assert socket.getaddrinfo is not None  # restored afterwards


def test_known_values_are_typed():
    fields, extra = sandbox.known_inputs(["Mira Vellamore", "1999-07-14", "m@example.invalid", "+61 400 000 000",
                                          "country=Utopia", "id=Z1234567", "place=Zenith Harbour"])
    assert fields == {"applicant_name": "Mira Vellamore", "date_of_birth": "1999-07-14", "email": "m@example.invalid",
                      "phone": "+61 400 000 000", "residential_country": "Utopia"}
    assert sorted((k.token_type, k.value) for k in extra) == [("ID", "Z1234567"), ("PLACE", "Zenith Harbour")]


# ---------------------------------------------------------------- wipe


def test_wipe_asks_first_then_deletes_everything(env):
    run(env)
    files = [p for p in env["root"].rglob("*") if p.is_file()]
    assert files
    lines: list[str] = []
    assert sandbox.wipe(root=env["root"], echo=lines.append, ask=lambda _: "no") == 0
    assert all(p.exists() for p in files)
    assert sandbox.wipe(root=env["root"], echo=lines.append, ask=lambda _: "WIPE") == len(files)
    assert not [p for p in env["root"].rglob("*") if p.is_file()]
    assert (env["root"] / "input").is_dir() and (env["root"] / "output").is_dir()
    no_personal("\n".join(lines))


# ---------------------------------------------------------------- local OCR (needs Tesseract; skipped otherwise)


def _ocr_ready() -> bool:
    from redaction.ocr import available

    return available() is not None


needs_ocr = pytest.mark.skipif(not _ocr_ready(), reason="local Tesseract not installed")


@needs_ocr
@pytest.mark.parametrize("name,maker", [("scan.png", fp.passport_png), ("scan.pdf", fp.passport_scanned_pdf)])
def test_ocr_reads_a_scan_and_nothing_personal_reaches_the_llm_text(env, name, maker):
    env["src"] = env["src"].with_name(name)
    env["src"].write_bytes(maker())
    out, stdout = run(env, "country=Utopia", ocr=True)
    meta = json.loads((out / "meta.json").read_text())
    assert meta["text_source"] == "local OCR" and meta["ocr"]["mean_confidence"] > 0
    redacted = (out / "2_redacted.txt").read_text()
    # Safety property: every personal value is either redacted, or the leak scan blocks the document.
    if meta["leak_pass"]:
        no_personal(redacted)
    no_personal(stdout)
    no_personal((out / "3_report.html").read_text())
    assert "[MRZ_1]" in redacted and "Text read by local OCR" in (out / "3_report.html").read_text()


def test_misread_mrz_digits_are_only_used_when_check_digits_validate():
    from redaction.passport import normalise_td3_line2

    l1, l2 = fp.mrz()
    misread = l2[:10] + "UT0" + l2[13:42] + "Q" + l2[43:]  # O read as 0 (letter field), 0 read as Q (digit field)
    assert normalise_td3_line2(misread) == l2
    p = parse_passport(f"{l1}\n{misread}\n")
    assert p.td3_parsed and all(p.checks_ok.values()) and "document_number" in p.derived
    wrong = l2[:13] + "8" + l2[14:]  # a digit misread as another digit: the DOB check digit fails, so DOB is not used
    p = parse_passport(f"{l1}\n{wrong}\n")
    assert not p.checks_ok["date_of_birth"] and "date_of_birth" not in p.derived


def test_garbled_mrz_left_by_ocr_fails_closed():
    from redaction.ocr import mrz_like

    assert mrz_like("P«UTOVELLAM0RE«MIRA<SOLENNE KKKKKKKKKKKKKK")
    assert not mrz_like("Date of expiry / Date d'expiration: 02 MAR/MAR 2031")


def test_year_first_slash_dates_are_a_date_of_birth_not_a_name():
    from redaction.recognizers import _date_variants

    fields, extra = sandbox.known_inputs(["Sita Example", "2003/03/14"])
    assert fields == {"applicant_name": "Sita Example", "date_of_birth": "2003/03/14"} and not extra
    assert {"2003/03/14", "2003-03-14", "14/03/2003", "14 March 2003"} <= set(_date_variants("2003/03/14"))
