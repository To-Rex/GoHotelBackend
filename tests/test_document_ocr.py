"""Hujjat skaneri ajratgichlari — modelsiz, sof mantiq testlari.

OCR modellari bu yerda ishga tushmaydi: tekshirilayotgani MRZ nazorat
raqamlari, JSHSHIR tuzilishi va yorliq mosligi — ular deterministik va
CI'da bir necha millisekundda ishlaydi.
"""
import pytest

from app.application.services.document_ocr import mrz, visual


def build_td1(
    doc="AA1234567",
    pinfl="31503900010015",
    birth="900315",
    expiry="300101",
    sex="M",
    nationality="UZB",
    state="UZB",
    surname="TOSHMATOV",
    given="JASUR<AKMALOVICH",
):
    line1 = f"ID{state}{doc}{mrz.check_digit(doc)}{pinfl}".ljust(30, "<")
    line2 = (
        f"{birth}{mrz.check_digit(birth)}{sex}"
        f"{expiry}{mrz.check_digit(expiry)}{nationality}"
    ).ljust(29, "<")
    line2 += mrz.check_digit(line1[5:30] + line2[0:7] + line2[8:15] + line2[18:29])
    return [line1, line2, f"{surname}<<{given}".ljust(30, "<")]


def build_td3(
    doc="AB1234567",
    pinfl="31503900010015",
    birth="900315",
    expiry="300101",
    sex="M",
    nationality="UZB",
    state="UZB",
    surname="TOSHMATOV",
    given="JASUR",
):
    line1 = f"P<{state}{surname}<<{given}".ljust(44, "<")
    optional = pinfl.ljust(14, "<")
    body = (
        f"{doc}{mrz.check_digit(doc)}{nationality}{birth}{mrz.check_digit(birth)}"
        f"{sex}{expiry}{mrz.check_digit(expiry)}{optional}{mrz.check_digit(optional)}"
    )
    return [line1, body + mrz.check_digit(body[0:10] + body[13:20] + body[21:43])]


class TestCheckDigit:
    @pytest.mark.parametrize(
        "value,expected",
        [("D23145890734", "9"), ("740812", "2"), ("<<<<<<", "0"), ("AA1234567", "8")],
    )
    def test_icao_examples(self, value, expected):
        assert mrz.check_digit(value) == expected


class TestTd1:
    def test_clean_card_is_fully_verified(self):
        result = mrz.parse_lines(build_td1())
        assert result.mrz_format == "TD1"
        assert result.verified
        assert result.fields["documentNumber"] == "AA1234567"
        assert result.fields["personalNumber"] == "31503900010015"
        assert result.fields["birthDate"] == "1990-03-15"
        assert result.fields["expiryDate"] == "2030-01-01"
        assert result.fields["lastName"] == "Toshmatov"
        assert result.fields["firstName"] == "Jasur Akmalovich"
        assert result.fields["nationality"] == "UZB"

    def test_ocr_spaces_and_stray_characters_are_tolerated(self):
        lines = build_td1()
        noisy = ["IDUZB AA1234567 8 31503900010015", lines[1], lines[2].rstrip("<")]
        result = mrz.parse_lines(noisy)
        assert result.verified
        assert result.fields["documentNumber"] == "AA1234567"

    def test_numeric_coercion_is_not_treated_as_a_guess(self):
        """Sana faqat raqam bo'lishi SHART — "9OO315" ni tuzatish taxmin emas."""
        lines = build_td1()
        lines[1] = "9OO315" + lines[1][6:]
        result = mrz.parse_lines(lines)
        assert result.fields["birthDate"] == "1990-03-15"
        assert result.guessed_fields == []
        assert result.verified

    def test_single_substitution_is_reported_and_never_verified(self):
        """Nazorat raqami bir xonali — tasodifiy nomzod ham 10 tadan bir marta
        to'g'ri chiqadi, shuning uchun bunday tiklash tasdiq bermaydi."""
        lines = build_td1()
        lines[0] = lines[0].replace("AA1234567", "AA1Z34567", 1)
        result = mrz.parse_lines(lines)
        assert result.fields["documentNumber"] == "AA1234567"
        assert result.checks_ok
        assert result.guessed_fields == ["documentNumber"]
        assert not result.verified

    def test_three_errors_do_not_claim_verification(self):
        lines = build_td1()
        lines[0] = lines[0].replace("AA1234567", "A41Z34S67", 1)
        result = mrz.parse_lines(lines)
        assert not result.verified


class TestTd3:
    def test_biometric_passport(self):
        result = mrz.parse_lines(build_td3())
        assert result.mrz_format == "TD3"
        assert result.verified
        assert result.fields["documentNumber"] == "AB1234567"
        assert result.fields["personalNumber"] == "31503900010015"
        assert result.fields["lastName"] == "Toshmatov"
        assert result.fields["firstName"] == "Jasur"

    def test_empty_optional_field_yields_no_personal_number(self):
        result = mrz.parse_lines(build_td3(pinfl=""))
        assert result.verified
        assert result.fields["personalNumber"] is None


class TestDates:
    def test_birth_date_never_lands_in_the_future(self):
        assert mrz.parse_date("991231", future=False) == "1999-12-31"

    def test_expiry_stays_in_the_future(self):
        assert mrz.parse_date("301231", future=True) == "2030-12-31"

    @pytest.mark.parametrize("value", ["991331", "000000", "abcdef"])
    def test_impossible_dates_are_rejected(self, value):
        assert mrz.parse_date(value, future=False) is None


class TestMrzDetection:
    def test_mrz_line_recognised(self):
        assert mrz.looks_like_mrz(build_td1()[2])

    @pytest.mark.parametrize("text", ["FAMILIYASI / SURNAME", "AA1234567", "Toshmatov"])
    def test_ordinary_text_is_not_mrz(self, text):
        assert not mrz.looks_like_mrz(text)


class TestPinfl:
    def test_valid_pinfl_yields_birth_date(self):
        assert visual.valid_pinfl("31503900010015")
        assert visual.pinfl_birth_date("31503900010015") == "1990-03-15"

    @pytest.mark.parametrize(
        "value",
        [
            "01503900010015",  # birinchi raqam 0 — jins/asr yaroqsiz
            "71503900010015",  # birinchi raqam 7 — yaroqsiz
            "39903900010015",  # 99-oy
            "3150390001001",  # 13 raqam
            "315039000100155",  # 15 raqam
        ],
    )
    def test_invalid_pinfl_rejected(self, value):
        assert not visual.valid_pinfl(value)

    def test_pinfl_found_inside_grouped_ocr_digits(self):
        assert visual.find_pinfl("JSHSHIR 3150 3900 0100 15") == "31503900010015"


class TestDocumentNumber:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("AA1234567", "AA1234567"),
            ("AA 1234567", "AA1234567"),
            ("Seriya AB-7654321", "AB7654321"),
        ],
    )
    def test_uzbek_shape_is_found(self, text, expected):
        assert visual.find_document_number(text) == expected

    @pytest.mark.parametrize("text", ["DOCUMENT No", "ID 1234", "12345678901234"])
    def test_labels_and_bare_digits_are_not_document_numbers(self, text):
        assert visual.find_document_number(text) is None


class TestLabels:
    def test_patronymic_label_never_registers_as_first_name(self):
        """"OTASINING ISMI" ichida "ISMI" bor — eng uzun moslik ustun bo'lmasa,
        otasining ismi ism maydoniga tushib qoladi."""
        assert visual.labels_in("OTASINING ISMI / PATRONYMIC") == ["patronymic"]
        assert visual.labels_in("ISMI / GIVEN NAMES") == ["firstName"]

    def test_labels_match_without_spaces(self):
        """Server OCR yorliqni bo'shliqsiz qaytaradi — shakl shunga moslashgan."""
        assert visual.labels_in("OTASININGISMI/PATRONYMIC") == ["patronymic"]
        assert visual.labels_in("IDKARTARAQAMI/DOCUMENTNO") == ["documentNumber"]
        assert visual.labels_in("JSHSHIR/PINFL") == ["personalNumber"]

    def test_damaged_labels_still_match(self):
        assert visual.labels_in("FAMLIYASISURNAME") == ["lastName"]
        assert visual.labels_in("ISMIGIVENHAUES") == ["firstName"]

    def test_values_are_not_labels(self):
        for value in ("TOSHMATOV", "JASUR", "AA1234567", "15.03.1990"):
            assert not visual.is_label(value), value

    def test_stop_words_are_not_names(self):
        assert not visual.looks_like_name("O'ZBEKISTON RESPUBLIKASI")
        assert not visual.looks_like_name("PASSPORT")
        assert visual.looks_like_name("Toshmatov")


class TestVisualDates:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("15.03.1990", "1990-03-15"),
            ("15/03/1990", "1990-03-15"),
            ("1990-03-15", "1990-03-15"),
            ("15 MAR 1990", "1990-03-15"),
        ],
    )
    def test_accepted_formats(self, text, expected):
        assert visual.parse_date(text) == expected


# --------------------------------------------------------------- OCR dvigateli
# Quyidagilar haqiqiy ONNX modellarni yuklaydi. Modellar bo'lmasa (masalan
# yengil CI konteynerida) o'tkazib yuboriladi.

engine = pytest.importorskip(
    "app.application.services.document_ocr.engine",
    reason="OCR dvigateli mavjud emas",
)
pytestmark_engine = pytest.mark.skipif(
    not engine.engine_importable(), reason="OCR modellari topilmadi"
)


def _text_line(text: str, height: int = 64):
    """Bitta matn qatorini rasmga aylantiradi (shriftsiz muhitda o'tkaziladi)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for name in ("consola.ttf", "cour.ttf", "DejaVuSansMono.ttf", "arial.ttf"):
        for base in (r"C:\Windows\Fonts", "/usr/share/fonts/truetype/dejavu"):
            import os

            path = os.path.join(base, name)
            if os.path.exists(path):
                font = ImageFont.truetype(path, int(height * 0.62))
                break
        if font:
            break
    if font is None:
        pytest.skip("Sinov uchun shrift topilmadi")

    import cv2

    width = max(60, int(font.getlength(text)) + 24)
    image = Image.new("RGB", (width, height), (250, 250, 248))
    ImageDraw.Draw(image).text((12, int(height * 0.16)), text, font=font, fill=(20, 20, 24))
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


@pytestmark_engine
class TestRecognitionBatching:
    """Partiyalash natijalarni asl kirishga qaytarishi shart.

    Kesimlar to'ldirishni kamaytirish uchun kengligi bo'yicha saralanadi, ya'ni
    modelga boshqa tartibda beriladi. Agar natija asl indeksga qaytarilmasa,
    familiya hujjat raqami maydoniga tushib ketadi va bu xato jimgina o'tadi.
    """

    TEXTS = [
        "TOSHMATOV",
        "JASUR",
        "AA1234567",
        "31503900010015",
        "15.03.1990",
        "AKMALOVICH",
        "UZB",
        "RASULOV",
    ]

    @pytest.mark.parametrize("batch_size", [1, 3, 16])
    def test_results_stay_aligned_with_inputs(self, batch_size):
        pytest.importorskip("PIL")
        crops = [_text_line(text) for text in self.TEXTS]
        results = engine.recognize(crops, batch_size=batch_size)
        assert len(results) == len(self.TEXTS)
        for expected, (got, _confidence) in zip(self.TEXTS, results):
            assert got.replace(" ", "").upper() == expected.replace(" ", "").upper()
