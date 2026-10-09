"""A FAKE passport-like text for tests. Every value is invented.

Uses ICAO's fictional specimen state "UTO" (Utopia) and an MRZ with correct
check digits, so the parser is exercised the way a real TD3 passport would.
"""

from __future__ import annotations

from redaction.passport import _check_digit

SURNAME, GIVEN = "VELLAMORE", "MIRA SOLENNE"
NUMBER = "X4R9T2Q71"
DOB_ISO, DOB_YYMMDD, DOB_PRINTED = "1999-07-14", "990714", "14 JUL/JUL 1999"
EXPIRY_YYMMDD, EXPIRY_PRINTED = "310302", "02 MAR/MAR 2031"
ISSUE_PRINTED = "03 MAR/MAR 2021"
PLACE_OF_BIRTH = "ZENITH HARBOUR"


def mrz() -> tuple[str, str]:
    names = f"{SURNAME}<<{GIVEN.replace(' ', '<')}"
    line1 = f"P<UTO{names}".ljust(44, "<")
    personal = "<" * 14
    body = (f"{NUMBER}{_check_digit(NUMBER)}UTO{DOB_YYMMDD}{_check_digit(DOB_YYMMDD)}F"
            f"{EXPIRY_YYMMDD}{_check_digit(EXPIRY_YYMMDD)}{personal}{_check_digit(personal)}")
    composite = body[0:10] + body[13:20] + body[21:43]
    line2 = body + str(_check_digit(composite))
    assert len(line1) == 44 and len(line2) == 44, (len(line1), len(line2))
    return line1, line2


def passport_text() -> str:
    l1, l2 = mrz()
    return (
        "SPECIMEN - FICTIONAL - NOT A REAL DOCUMENT\n"
        "REPUBLIC OF UTOPIA / PASSPORT\n"
        "Type / Type: P          Code / Code: UTO\n"
        f"Passport No. / No. du passeport: {NUMBER}\n"
        f"Surname / Nom: {SURNAME}\n"
        f"Given names / Prénoms: {GIVEN}\n"
        "Nationality / Nationalité: UTOPIAN\n"
        f"Date of birth / Date de naissance: {DOB_PRINTED}\n"
        "Sex / Sexe: F\n"
        f"Place of birth / Lieu de naissance\n{PLACE_OF_BIRTH}\n"
        f"Date of issue / Date de délivrance: {ISSUE_PRINTED}\n"
        f"Date of expiry / Date d'expiration: {EXPIRY_PRINTED}\n"
        "Authority / Autorité: Ministry of Foreign Affairs\n"
        "Holder's signature: [signed]\n"
        "\n"
        f"{l1}\n{l2}\n"
    )


PERSONAL_VALUES = [SURNAME, "Mira", "Solenne", NUMBER, DOB_PRINTED, PLACE_OF_BIRTH, *mrz()]


def passport_image(scale: float = 1.0):
    """The fake passport drawn as a scan-like greyscale image (PIL). Fictional values only."""
    from PIL import Image, ImageDraw, ImageFont

    font_path = "/System/Library/Fonts/Menlo.ttc"
    size = int(34 * scale)
    font = ImageFont.truetype(font_path, size)
    mrz_font = ImageFont.truetype(font_path, int(40 * scale))
    lines = passport_text().strip().splitlines()
    body, mrz_lines = [l for l in lines if not l.startswith(("P<", NUMBER))], list(mrz())
    w, h = int(2400 * scale), int((80 + 52 * len(body) + 260) * scale)
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    y = int(60 * scale)
    for line in body:
        d.text((int(60 * scale), y), line, fill=0, font=font)
        y += int(52 * scale)
    y = h - int(170 * scale)
    for line in mrz_lines:
        d.text((int(60 * scale), y), line, fill=0, font=mrz_font)
        y += int(70 * scale)
    return img


def passport_png(scale: float = 1.0) -> bytes:
    import io

    buf = io.BytesIO()
    passport_image(scale).save(buf, format="PNG")
    return buf.getvalue()


def passport_scanned_pdf() -> bytes:
    """An image-only PDF (no text layer), like a scanner produces."""
    import io

    buf = io.BytesIO()
    passport_image().convert("RGB").save(buf, format="PDF", resolution=200)
    return buf.getvalue()
