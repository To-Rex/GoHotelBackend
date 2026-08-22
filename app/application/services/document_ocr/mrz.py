"""MRZ (Machine Readable Zone) o'qish va ICAO 9303 bo'yicha tekshirish.

MRZ — hujjatdagi eng ishonchli manba: har maydonning o'z nazorat raqami bor,
shuning uchun natijani shunchaki "o'qib" qolmay, MATEMATIK TASDIQLASH mumkin.
Bu yerdagi asosiy g'oya — nazorat raqamini faqat rad etish uchun emas, xatoni
TUZATISH uchun ishlatish: OCR-B shriftida chalkashadigan belgilar juftliklari
ma'lum (O/0, I/1, S/5, B/8...), maydon qisqa, shuning uchun nazorat raqami
to'g'ri chiqadigan variantni izlash arzon va deterministik.

Qo'llab-quvvatlanadigan formatlar:
  TD1 — ID karta orqa tomoni, 3 x 30 (O'zbek ID kartasida JSHSHIR shu yerda)
  TD2 — 2 x 36
  TD3 — biometrik passport ma'lumotlar sahifasi, 2 x 44
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

FILLER = "<"

# Nazorat raqami uchun belgi qiymatlari (ICAO 9303): raqam — o'zi,
# harf — 10..35, to'ldiruvchi "<" — 0
_VALUES = {str(d): d for d in range(10)}
_VALUES.update({chr(ord("A") + i): 10 + i for i in range(26)})
_VALUES[FILLER] = 0

_WEIGHTS = (7, 3, 1)

# OCR-B da bir-biriga o'xshab ketadigan belgilar. Yo'nalishli: kalit — o'qilgan
# belgi, qiymat — u aslida nima bo'lishi mumkin.
_CONFUSIONS: dict[str, str] = {
    "0": "OQD",
    "O": "0DQ",
    "Q": "0O",
    "D": "0O",
    "1": "IL",
    "I": "1L",
    "L": "1I",
    "2": "Z",
    "Z": "2",
    "4": "A",
    "A": "4",
    "5": "S",
    "S": "5",
    "6": "G",
    "G": "6C",
    "8": "B",
    "B": "8",
    "7": "T",
    "T": "7",
    "U": "V",
    "V": "U",
    "K": "X",
    "X": "K",
    "M": "N",
    "N": "M",
    "C": "G",
}

# Faqat raqam bo'lishi shart bo'lgan maydonlar uchun harf -> raqam
_TO_DIGIT = str.maketrans(
    {"O": "0", "Q": "0", "D": "0", "U": "0", "I": "1", "L": "1", "Z": "2",
     "A": "4", "S": "5", "G": "6", "T": "7", "B": "8"}
)
# Faqat harf bo'lishi shart bo'lgan maydonlar uchun raqam -> harf
_TO_ALPHA = str.maketrans({"0": "O", "1": "I", "2": "Z", "4": "A", "5": "S", "6": "G", "8": "B"})

FORMAT_SHAPES = {"TD1": (3, 30), "TD2": (2, 36), "TD3": (2, 44)}


def check_digit(value: str) -> str:
    """ICAO 9303 nazorat raqami (7-3-1 og'irliklari bilan)."""
    total = 0
    for index, character in enumerate(value):
        total += _VALUES.get(character, 0) * _WEIGHTS[index % 3]
    return str(total % 10)


def normalize_line(raw: str, width: int) -> str:
    """OCR chiqishini MRZ qatoriga keltiradi.

    Bo'sh joy MRZ'da mavjud emas — OCR uni to'ldiruvchi "<" yoki umuman yo'q
    joydan o'ylab topadi, shuning uchun olib tashlanadi. Qolgan begona
    belgilar ham to'ldiruvchiga aylantiriladi, chunki ular deyarli har doim
    "<" ning noto'g'ri o'qilishi.
    """
    text = raw.upper().replace(" ", "").replace("\t", "")
    text = text.replace("«", "<<").replace("»", ">>")
    text = re.sub(r"[^A-Z0-9<]", FILLER, text)
    if len(text) > width:
        text = text[:width]
    return text.ljust(width, FILLER)


def looks_like_mrz(text: str) -> bool:
    """Qator MRZ'ga o'xshaydimi — to'liq tekshiruvdan oldingi arzon filtr."""
    compact = text.upper().replace(" ", "")
    if len(compact) < 20:
        return False
    allowed = sum(1 for c in compact if c in _VALUES)
    if allowed / len(compact) < 0.85:
        return False
    return FILLER * 2 in compact or compact.startswith(("P<", "ID", "I<", "AC")) or bool(
        re.search(r"\d{6}[0-9<][MFX<]\d{6}", compact)
    )


def parse_date(value: str, *, future: bool) -> str | None:
    """YYMMDD -> ISO. `future` — muddat sanasi (o'tmishga tushmasligi kerak)."""
    digits = value.translate(_TO_DIGIT)
    if not re.fullmatch(r"\d{6}", digits):
        return None
    year, month, day = int(digits[0:2]), int(digits[2:4]), int(digits[4:6])
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    current = date.today().year
    if future:
        # Muddat sanasi o'tmishda ham bo'lishi mumkin — hujjat muddati tugagan
        # bo'lsa aynan shuni ko'rsatish kerak. Uni "kelajakka" surib qo'yish
        # muddati o'tgan hujjatni amaldagidek ko'rsatib yuboradi. Faqat
        # haqiqatan ham mumkin bo'lmagan uzoq o'tmish keyingi asrga suriladi
        # (hujjat muddati odatda 10 yil, 30 yil oldin tugagani MRZ emas).
        full = 2000 + year
        if full < current - 30:
            full += 100
    else:
        # Tug'ilgan sana: kelajakka tushmasligi kerak
        full = 2000 + year
        if full > current:
            full -= 100
    try:
        date(full, month, day)
    except ValueError:
        return None
    return f"{full:04d}-{month:02d}-{day:02d}"


def _names(raw: str) -> tuple[str, str, str]:
    """`FAMILIYA<<ISM<OTASINING<ISMI` -> (familiya, ism, otasining ismi).

    O'zbek hujjatlarida ism maydonida ism va otasining ismi ketma-ket yoziladi.
    Ularni bitta satrga qo'shib yuborish mehmon kartochkasiga "Jasur
    Akmalovich" deb yozib qo'yadi, holbuki formada ism uchun alohida maydon
    bor — shuning uchun birinchi so'z ism, qolgani otasining ismi.

    Ism qatorining nazorat raqami yo'q, ya'ni uni matematik tekshirib
    bo'lmaydi. Shuning uchun bitta harfdan iborat "so'zlar" tashlanadi: ICAO
    ismlarni to'liq yozadi, bosh harf bilan qisqartirmaydi, demak yakka harf
    deyarli har doim to'ldiruvchi "<" ning noto'g'ri o'qilishi ("BEKZOD<K").
    """
    cleaned = raw.rstrip(FILLER)
    surname, _, given = cleaned.partition(FILLER * 2)

    def to_words(part: str) -> list[str]:
        words = [word for word in part.split(FILLER) if word]
        meaningful = [word for word in words if len(word) > 1]
        # Butun bo'lak yakka harflardan iborat bo'lsa, uni yo'qotib qo'ymaymiz
        return meaningful or words

    surname_words = to_words(surname)
    given_words = to_words(given)
    if not given_words and len(surname_words) > 1:
        # Ikkilangan to'ldiruvchi o'qilmagan — birinchi so'z familiya deb olinadi
        surname_words, given_words = surname_words[:1], surname_words[1:]
    return (
        " ".join(surname_words),
        given_words[0] if given_words else "",
        " ".join(given_words[1:]),
    )


def title_case(value: str) -> str:
    return " ".join(part.capitalize() for part in value.split() if part)


@dataclass
class MrzResult:
    fields: dict = field(default_factory=dict)
    mrz_format: str = ""
    checks_passed: int = 0
    checks_total: int = 0
    #: Nazorat raqami faqat BELGI ALMASHTIRISH taxmini bilan to'g'ri chiqqan
    #: maydonlar. Ular mustaqil manba bilan tasdiqlanmaguncha ishonchli emas.
    guessed_fields: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    @property
    def checks_ok(self) -> bool:
        """Barcha nazorat raqamlari to'g'ri (taxmindan keyin bo'lsa ham)."""
        return self.checks_total > 0 and self.checks_passed == self.checks_total

    @property
    def verified(self) -> bool:
        """Taxminsiz, to'liq matematik tasdiqlangan MRZ."""
        return self.checks_ok and not self.guessed_fields

    @property
    def score(self) -> float:
        return self.checks_passed / self.checks_total if self.checks_total else 0.0


def _repair_field(value: str, expected: str, *, numeric: bool) -> tuple[str, bool] | None:
    """Nazorat raqami to'g'ri chiqadigan variantni izlaydi.

    Ikki bosqich, va ular ataylab teng emas:

    1. MAJBURIY TUZATISH — sana maydoni faqat raqamdan iborat bo'lishi SHART,
       shuning uchun "9OO315" -> "900315" taxmin emas, formatning talabi. Bu
       ishonchli, natija tasdiqlangan deb hisoblanaveradi.
    2. BITTA BELGINI ALMASHTIRISH — bu allaqachon taxmin. Nazorat raqami bir
       xonali, ya'ni tasodifiy nomzod ham 10 tadan bir marta "to'g'ri" chiqadi,
       yakuniy (composite) tekshiruv esa buni ushlay olmaydi: hujjat raqami
       composite ichida ham xuddi shu og'irliklar bilan tushadi, shuning uchun
       o'z nazorat raqamidan o'tgan xato composite'dan ham o'tib ketadi.
       Shu sababli bunday natija chaqiruvchiga "tuzatilgan" deb belgilanadi va
       to'liq tasdiqlangan hisoblanmaydi — uni faqat mustaqil manba (hujjatning
       bosma tomoni) tasdiqlashi mumkin.

    Ikkitalik almashtirish ataylab qilinmaydi: u yolg'on tuzatish ehtimolini
    to'g'ri tuzatishdan yuqoriga ko'taradi.

    Qaytaradi: (qiymat, taxmin_qilindimi) yoki None.
    """
    if check_digit(value) == expected:
        return value, False

    if numeric:
        coerced = value.translate(_TO_DIGIT)
        if coerced != value and check_digit(coerced) == expected:
            return coerced, False

    base = value.translate(_TO_DIGIT) if numeric else value
    for index, character in enumerate(base):
        for replacement in _CONFUSIONS.get(character, ""):
            if numeric and not replacement.isdigit():
                continue
            candidate = base[:index] + replacement + base[index + 1 :]
            if check_digit(candidate) == expected:
                return candidate, True
    return None


class _Reader:
    """Bitta MRZ maydonini o'qib, nazorat raqami bilan tekshiradi/tuzatadi."""

    def __init__(self) -> None:
        self.passed = 0
        self.total = 0
        self.guessed: list[str] = []

    def checked(self, name: str, value: str, expected_digit: str, *, numeric: bool) -> str:
        self.total += 1
        expected = expected_digit.translate(_TO_DIGIT)
        repair = _repair_field(value, expected, numeric=numeric)
        if repair is None:
            return value.translate(_TO_DIGIT) if numeric else value
        fixed, guessed = repair
        self.passed += 1
        if guessed:
            self.guessed.append(name)
        return fixed


def _parse_td1(lines: list[str]) -> MrzResult:
    line1, line2, line3 = lines
    reader = _Reader()

    document_number = reader.checked("documentNumber", line1[5:14], line1[14], numeric=False)
    optional1 = line1[15:30]
    birth = reader.checked("birthDate", line2[0:6], line2[6], numeric=True)
    expiry = reader.checked("expiryDate", line2[8:14], line2[14], numeric=True)

    # Yakuniy nazorat raqami TUZATILGAN qatorlar ustidan hisoblanadi. Aks holda
    # bitta maydon tuzatilgan hujjat hech qachon to'liq tasdiqlanmaydi — va
    # aynan shu tekshiruv tuzatishning to'g'riligini mustaqil isbotlaydi.
    fixed1 = line1[:5] + document_number + line1[14:]
    fixed2 = birth + line2[6:8] + expiry + line2[14:]
    composite_source = fixed1[5:30] + fixed2[0:7] + fixed2[8:15] + fixed2[18:29]
    reader.total += 1
    if check_digit(composite_source) == line2[29].translate(_TO_DIGIT):
        reader.passed += 1

    surname, given, patronymic = _names(line3)
    # O'zbek ID kartasida JSHSHIR ixtiyoriy maydonda (14 raqam) turadi.
    # Harflarni raqamga MAJBURLAMAYMIZ: chet el hujjatlarida bu maydonda
    # harfli ma'lumot bo'ladi va uni raqamga aylantirish 14 xonali soxta
    # JSHSHIR yasab qo'yadi.
    personal = optional1.replace(FILLER, "").strip()
    personal = personal if re.fullmatch(r"\d{14}", personal) else ""
    fields = {
        "documentNumber": document_number.replace(FILLER, ""),
        "birthDate": parse_date(birth, future=False),
        "expiryDate": parse_date(expiry, future=True),
        "sex": line2[7] if line2[7] in "MF" else None,
        "nationality": line2[15:18].translate(_TO_ALPHA).replace(FILLER, ""),
        "issuingCountry": line1[2:5].translate(_TO_ALPHA).replace(FILLER, ""),
        "lastName": title_case(surname),
        "firstName": title_case(given),
        "patronymic": title_case(patronymic) or None,
        "personalNumber": personal or None,
    }
    return MrzResult(fields, "TD1", reader.passed, reader.total, reader.guessed, lines)


def _parse_td2_td3(lines: list[str], mrz_format: str) -> MrzResult:
    line1, line2 = lines
    reader = _Reader()
    name_field = line1[5:]

    document_number = reader.checked("documentNumber", line2[0:9], line2[9], numeric=False)
    birth = reader.checked("birthDate", line2[13:19], line2[19], numeric=True)
    expiry = reader.checked("expiryDate", line2[21:27], line2[27], numeric=True)

    personal = None
    optional_value = line2[28:42] if mrz_format == "TD3" else line2[28:35]
    if mrz_format == "TD3" and optional_value.strip(FILLER):
        optional_value = reader.checked("personalNumber", optional_value, line2[42], numeric=False)
        # Harflarni raqamga MAJBURLAMAYMIZ: chet el passportlarida bu maydonda
        # harfli ma'lumot bo'ladi va uni raqamga aylantirish soxta JSHSHIR
        # yasab qo'yadi — u tuzilish tekshiruvidan ham o'tib ketishi mumkin.
        digits = optional_value.replace(FILLER, "").strip()
        personal = digits if re.fullmatch(r"\d{14}", digits) else None

    # Yakuniy nazorat raqami TUZATILGAN maydonlar ustidan — u tuzatishning
    # to'g'riligini mustaqil ravishda tasdiqlaydi (TD1 dagi bilan bir xil sabab).
    fixed2 = (
        document_number
        + line2[9:13]
        + birth
        + line2[19:21]
        + expiry
        + line2[27:28]
        + optional_value
        + line2[28 + len(optional_value) :]
    )
    if mrz_format == "TD3":
        composite_source = fixed2[0:10] + fixed2[13:20] + fixed2[21:43]
        composite_digit = line2[43]
    else:
        composite_source = fixed2[0:10] + fixed2[13:20] + fixed2[21:35]
        composite_digit = line2[35]

    reader.total += 1
    if check_digit(composite_source) == composite_digit.translate(_TO_DIGIT):
        reader.passed += 1

    surname, given, patronymic = _names(name_field)
    fields = {
        "documentNumber": document_number.replace(FILLER, ""),
        "birthDate": parse_date(birth, future=False),
        "expiryDate": parse_date(expiry, future=True),
        "sex": line2[20] if line2[20] in "MF" else None,
        "nationality": line2[10:13].translate(_TO_ALPHA).replace(FILLER, ""),
        "issuingCountry": line1[2:5].translate(_TO_ALPHA).replace(FILLER, ""),
        "lastName": title_case(surname),
        "firstName": title_case(given),
        "patronymic": title_case(patronymic) or None,
        "personalNumber": personal,
    }
    return MrzResult(fields, mrz_format, reader.passed, reader.total, reader.guessed, lines)


def _compact_length(line: str) -> int:
    return len(re.sub(r"[^A-Z0-9<]", "", line.upper().replace(" ", "")))


def parse_lines(raw_lines: list[str]) -> MrzResult | None:
    """OCR bergan qatorlardan MRZ'ni ajratadi.

    Format tanlashda QATOR UZUNLIGI hal qiluvchi ahamiyatga ega, chunki TD2 va
    TD3 ning dastlabki 28 belgisi bir xil joylashgan: hujjat raqami, fuqarolik,
    sana va muddat maydonlari aynan bir xil o'rinda turadi. Shu sababli TD3
    qatori TD2 sifatida ham nazorat raqamlaridan o'tib ketishi mumkin — va
    o'tsa, passportning JSHSHIR'i yo'qoladi, chunki u faqat TD3'da bor.
    Uzunlik esa ularni aniq ajratadi: 44 va 36.
    """
    candidates = [line for line in (l.strip() for l in raw_lines) if line]
    if not candidates:
        return None

    best: MrzResult | None = None
    best_rank: tuple = ()
    for mrz_format, (count, width) in FORMAT_SHAPES.items():
        if len(candidates) < count:
            continue
        # MRZ hujjatning pastida — oxirgi `count` qator olinadi
        selected = candidates[-count:]
        # OCR oxirgi to'ldiruvchilarni tushirib qoldirishi mumkin, shuning uchun
        # eng uzun qator olinadi: u odatda raqam bilan tugaydi va to'liq chiqadi.
        observed = max(_compact_length(line) for line in selected)
        length_fit = 1 if abs(observed - width) <= 2 else 0
        normalized = [normalize_line(line, width) for line in selected]
        try:
            if mrz_format == "TD1":
                result = _parse_td1(normalized)
            else:
                result = _parse_td2_td3(normalized, mrz_format)
        except (IndexError, ValueError):
            continue
        has_birth = 1 if result.fields.get("birthDate") else 0
        rank = (has_birth, length_fit, result.score, result.checks_total)
        if best is None or rank > best_rank:
            best = result
            best_rank = rank
    return best


def parse_text(text: str) -> MrzResult | None:
    """Erkin matndan MRZ'ga o'xshash qatorlarni ajratib o'qiydi."""
    lines = [line for line in text.splitlines() if looks_like_mrz(line)]
    return parse_lines(lines) if lines else None
