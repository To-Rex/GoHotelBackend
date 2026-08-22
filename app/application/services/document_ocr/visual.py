"""Hujjatning BOSMA tomonidan maydonlarni ajratish.

MRZ bo'lmagan yoki o'chib ketgan holatlar uchun, hamda MRZ natijasini mustaqil
tasdiqlash uchun kerak — MRZ'dagi belgi almashtirish taxminini faqat shu yerdan
kelgan qiymat tasdiqlay oladi.

Server OCR'ining brauzerdagi Tesseract'dan muhim farqi bor: PP-OCR butun
yorliqni bitta blok qilib, so'z oralaridagi bo'shliqsiz qaytaradi
("IDKARTARAQAMI/DOCUMENTNO"). Shuning uchun yorliqlar bo'sh joyi OLIB
TASHLANGAN shaklda solishtiriladi — bu ikkala ko'rinishni ham qamrab oladi.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Har maydon uchun ikki tilli yorliq variantlari (o'zbek lotin/kirill, ingliz)
LABELS: dict[str, list[str]] = {
    "lastName": ["FAMILIYASI", "FAMILIYA", "SURNAME", "FAMILY NAME", "ФАМИЛИЯСИ", "ФАМИЛИЯ"],
    "firstName": ["ISMI", "GIVEN NAMES", "GIVEN NAME", "FIRST NAME", "FORENAMES", "ИСМИ", "ИМЯ"],
    "patronymic": [
        "OTASINING ISMI", "OTASINING ISM", "PATRONYMIC", "ОТАСИНИНГ ИСМИ", "ОТЧЕСТВО",
    ],
    "birthDate": [
        "TUGILGAN SANASI", "TUGILGAN SANA", "DATE OF BIRTH", "BIRTH DATE",
        "ТУГИЛГАН САНАСИ", "ДАТА РОЖДЕНИЯ",
    ],
    "personalNumber": [
        "JSHSHIR", "JSHIR", "PINFL", "PINPP", "PERSONAL NUMBER", "PERSONAL NO",
        "ЖШШИР", "ПИНФЛ",
    ],
    "documentNumber": [
        "ID KARTA RAQAMI", "KARTA RAQAMI", "PASPORT RAQAMI", "SERIYA VA RAQAMI",
        "DOCUMENT NUMBER", "DOCUMENT NO", "PASSPORT NO", "CARD NO", "НОМЕР ДОКУМЕНТА",
    ],
    "expiryDate": [
        "AMAL QILISH MUDDATI", "AMAL QILISH", "DATE OF EXPIRY", "EXPIRY", "ГОДЕН ДО",
    ],
    "issueDate": ["BERILGAN SANASI", "BERILGAN SANA", "DATE OF ISSUE", "ВЫДАН"],
    "sex": ["JINSI", "SEX", "GENDER", "ЖИНСИ", "ПОЛ"],
    "nationality": [
        "MILLATI", "NATIONALITY", "FUQAROLIGI", "CITIZENSHIP", "МИЛЛАТИ", "ГРАЖДАНСТВО",
    ],
    "birthPlace": ["TUGILGAN JOYI", "PLACE OF BIRTH", "ТУГИЛГАН ЖОЙИ", "МЕСТО РОЖДЕНИЯ"],
}

# Ism bo'la olmaydigan so'zlar — hujjat sarlavhalari, davlat nomi, jins
STOP_WORDS = {
    "OZBEKISTON", "UZBEKISTAN", "RESPUBLIKASI", "REPUBLIC", "PASPORT", "PASSPORT",
    "IDENTITY", "CARD", "KARTA", "ID", "TYPE", "TURI", "CODE", "KOD", "MAMLAKAT",
    "COUNTRY", "SIGNATURE", "IMZO", "UZB", "ERKAK", "AYOL", "MALE", "FEMALE",
    "OZBEK", "RUS", "TOJIK", "QOZOQ", "QORAQALPOQ",
}

_APOSTROPHES = "'‘’ʻʼ´`"


def norm(value: str) -> str:
    """Solishtirish uchun normal shakl: diakritika va tinish belgilarisiz."""
    text = value.upper()
    for character in _APOSTROPHES:
        text = text.replace(character, "")
    text = re.sub(r"[^A-ZА-ЯЁ0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compact(value: str) -> str:
    """Bo'shliqsiz normal shakl — OCR so'z oralarini yo'qotgan holat uchun."""
    return norm(value).replace(" ", "")


def edit_distance(a: str, b: str, max_distance: int) -> int:
    """Levenshtein masofasi; chegaradan oshsa erta to'xtaydi."""
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
        if min(current) > max_distance:
            return max_distance + 1
        previous = current
    return previous[len(b)]


def allowed_typos(length: int) -> int:
    """Uzun yorliqda ko'proq xatoga yo'l qo'yiladi; qisqasida umuman yo'q.

    Past sifatli rasmda uzun yorliq deyarli har doim bir-ikki belgisi bilan
    buzilib chiqadi ("GIVENNAMES" -> "GIVENHAUES"), qisqa yorliqda esa bitta
    xato ham uni butunlay boshqa maydonga aylantirib yuborishi mumkin.
    """
    if length >= 10:
        return 2
    if length >= 5:
        return 1
    return 0


#: (maydon, bo'shliqsiz yorliq) — eng uzunidan boshlab.
#: Tartib MUHIM: "OTASININGISMI" har doim "ISMI" dan oldin tekshirilishi shart,
#: aks holda otasining ismi ism maydoniga tushib qoladi.
_ALL_LABELS: list[tuple[str, str]] = sorted(
    ((field_name, compact(variant)) for field_name, variants in LABELS.items() for variant in variants),
    key=lambda item: len(item[1]),
    reverse=True,
)


def labels_in(text: str) -> list[str]:
    """Matndagi maydon yorliqlarini topadi (eng uzun moslik ustun).

    Topilgan bo'lak "band" qilinadi: "OTASINING ISMI / PATRONYMIC" ichida
    "ISMI" ham bor, band qilinmasa bu qator ham otasining ismi, ham ism
    yorlig'i deb hisoblanadi.
    """
    key = compact(text)
    if not key:
        return []
    claimed = [False] * len(key)
    found: list[str] = []
    for field_name, label in _ALL_LABELS:
        if field_name in found or not label:
            continue
        position = key.find(label)
        while position >= 0:
            if not any(claimed[position : position + len(label)]):
                for index in range(position, position + len(label)):
                    claimed[index] = True
                found.append(field_name)
                break
            position = key.find(label, position + 1)
        if field_name in found:
            continue
        # Aniq moslik bo'lmasa — buzib o'qilgan yorliqni taxminiy izlash
        tolerance = allowed_typos(len(label))
        if not tolerance:
            continue
        for start in range(0, max(1, len(key) - len(label) + 1)):
            window = key[start : start + len(label)]
            if any(claimed[start : start + len(label)]):
                continue
            if edit_distance(label, window, tolerance) <= tolerance:
                for index in range(start, start + len(label)):
                    claimed[index] = True
                found.append(field_name)
                break
    return found


def is_label(text: str) -> bool:
    return bool(labels_in(text))


# --------------------------------------------------------------- qiymat turlari

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_date(raw: str) -> str | None:
    """`15.03.1990`, `15/03/1990`, `1990-03-15`, `15 MAR 1990` -> ISO."""
    text = raw.strip().upper()
    match = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", text)
    if match:
        year, month, day = (int(g) for g in match.groups())
    else:
        match = re.search(r"(\d{1,2})[-./\s]+(\d{1,2})[-./\s]+(\d{4})", text)
        if match:
            day, month, year = (int(g) for g in match.groups())
        else:
            match = re.search(r"(\d{1,2})[-.\s]*([A-Z]{3})[-.\s]*(\d{4})", text)
            if not match:
                return None
            day = int(match.group(1))
            month = _MONTHS.get(match.group(2), 0)
            year = int(match.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2100):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def valid_pinfl(digits: str) -> bool:
    """JSHSHIR tuzilishi: 14 raqam, 1-raqam jins/asr (1..6), so'ng DDMMYY."""
    if not re.fullmatch(r"\d{14}", digits):
        return False
    century_sex = int(digits[0])
    if not 1 <= century_sex <= 6:
        return False
    day, month, year_short = int(digits[1:3]), int(digits[3:5]), int(digits[5:7])
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return False
    year = (1800 if century_sex <= 2 else 1900 if century_sex <= 4 else 2000) + year_short
    from calendar import monthrange

    return day <= monthrange(year, month)[1]


def pinfl_birth_date(digits: str) -> str | None:
    if not valid_pinfl(digits):
        return None
    century_sex = int(digits[0])
    year = (1800 if century_sex <= 2 else 1900 if century_sex <= 4 else 2000) + int(digits[5:7])
    return f"{year:04d}-{digits[3:5]}-{digits[1:3]}"


def find_pinfl(text: str) -> str | None:
    """Matndagi JSHSHIR — OCR raqamlarni guruhlab bersa ham topiladi."""
    digits_only = re.sub(r"[^0-9]", "", text)
    for start in range(0, max(1, len(digits_only) - 13)):
        candidate = digits_only[start : start + 14]
        if len(candidate) == 14 and valid_pinfl(candidate):
            return candidate
    return None


#: O'zbek hujjat raqami — ikki lotin harfi va yetti raqam (AA1234567).
#: Bu shakl juda o'ziga xos, shuning uchun umumiy evristikadan ancha ishonchli.
_DOC_NUMBER = re.compile(r"(?<![A-Z0-9])([A-Z]{2})[\s.\-]?(\d{7})(?![0-9])")

#: Yorliqning o'zidan raqam yasab yubormaslik uchun (masalan "DOCUMENT No")
_LABEL_TOKENS = {"NO", "NR", "ID", "PN", "SR", "N"}


def find_document_number(text: str) -> str | None:
    cleaned = norm(text)
    for match in _DOC_NUMBER.finditer(cleaned):
        prefix = match.group(1)
        if prefix in _LABEL_TOKENS:
            continue
        return f"{prefix}{match.group(2)}"
    return None


def title_case(value: str) -> str:
    return " ".join(part.capitalize() for part in value.split() if part)


def looks_like_name(text: str) -> bool:
    letters = norm(text)
    if not letters or any(character.isdigit() for character in letters):
        return False
    words = letters.split()
    if not words or len(letters.replace(" ", "")) < 2:
        return False
    if any(word in STOP_WORDS for word in words):
        return False
    return not is_label(text) and len(words) <= 4


# ------------------------------------------------------------------ tartiblash


@dataclass
class Region:
    text: str
    confidence: float
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return max(1.0, self.bottom - self.top)


def to_regions(raw_regions: list[dict]) -> list[Region]:
    regions: list[Region] = []
    for item in raw_regions:
        box = item["box"]
        regions.append(
            Region(
                text=item["text"],
                confidence=float(item.get("confidence", 0.0)),
                left=float(box[:, 0].min()),
                top=float(box[:, 1].min()),
                right=float(box[:, 0].max()),
                bottom=float(box[:, 1].max()),
            )
        )
    return regions


def _horizontal_overlap(a: Region, b: Region) -> float:
    span = min(a.right, b.right) - max(a.left, b.left)
    narrower = min(a.right - a.left, b.right - b.left)
    return span / narrower if narrower > 0 else 0.0


def _value_for(label: Region, regions: list[Region]) -> Region | None:
    """Yorliqqa tegishli qiymat blokini topadi.

    O'zbek hujjatlarida qiymat yorliqning ostida turadi, shuning uchun avval
    pastdagi eng yaqin va ustunlari ustma-ust tushadigan blok qaraladi. Ba'zi
    maketlarda qiymat o'ng yonda bo'lgani uchun zaxira sifatida shu ham
    tekshiriladi. Yorliqning o'zi hech qachon qiymat sifatida olinmaydi.
    """
    below: list[tuple[float, Region]] = []
    beside: list[tuple[float, Region]] = []
    for candidate in regions:
        if candidate is label or is_label(candidate.text):
            continue
        vertical_gap = candidate.top - label.bottom
        if -label.height * 0.4 <= vertical_gap <= label.height * 2.6:
            if _horizontal_overlap(label, candidate) >= 0.3:
                below.append((vertical_gap, candidate))
        if (
            abs(candidate.center_y - label.center_y) <= label.height * 0.7
            and candidate.left >= label.right - label.height * 0.2
        ):
            beside.append((candidate.left - label.right, candidate))
    if below:
        return min(below, key=lambda item: item[0])[1]
    if beside:
        return min(beside, key=lambda item: item[0])[1]
    return None


@dataclass
class VisualResult:
    fields: dict = field(default_factory=dict)
    #: Yorliq orqali emas, shakl orqali topilgan maydonlar (ular ishonchliroq)
    by_pattern: set = field(default_factory=set)
    text: str = ""

    @property
    def filled(self) -> int:
        return sum(1 for value in self.fields.values() if value)


def parse_regions(raw_regions: list[dict]) -> VisualResult:
    """Aniqlangan matn bloklaridan hujjat maydonlarini yig'adi."""
    regions = to_regions(raw_regions)
    full_text = "\n".join(region.text for region in regions)
    result = VisualResult(text=full_text)

    # 1) Shakl bo'yicha — yorliqqa umuman bog'liq emas, shuning uchun eng
    #    ishonchli yo'l: JSHSHIR 14 raqamli va o'z tuzilishi bilan tekshiriladi,
    #    hujjat raqami esa "ikki harf + yetti raqam" shakliga ega.
    pinfl = find_pinfl(re.sub(r"[^0-9]", "", full_text.replace("\n", "")))
    if pinfl:
        result.fields["personalNumber"] = pinfl
        result.by_pattern.add("personalNumber")
        birth = pinfl_birth_date(pinfl)
        if birth:
            result.fields["birthDate"] = birth
            result.by_pattern.add("birthDate")

    for region in regions:
        if is_label(region.text):
            continue
        number = find_document_number(region.text)
        if number:
            result.fields["documentNumber"] = number
            result.by_pattern.add("documentNumber")
            break

    # 2) Yorliq bo'yicha — ism, familiya va yorliqsiz topilmagan qolgan maydonlar
    for region in regions:
        for field_name in labels_in(region.text):
            value_region = _value_for(region, regions)
            if value_region is None:
                continue
            value = value_region.text.strip()
            if field_name in ("lastName", "firstName", "patronymic"):
                if looks_like_name(value):
                    result.fields.setdefault(field_name, title_case(norm(value)))
            elif field_name in ("birthDate", "expiryDate", "issueDate"):
                parsed = parse_date(value)
                if parsed and field_name not in result.by_pattern:
                    result.fields.setdefault(field_name, parsed)
            elif field_name == "personalNumber":
                candidate = find_pinfl(value)
                if candidate:
                    result.fields.setdefault("personalNumber", candidate)
            elif field_name == "documentNumber":
                candidate = find_document_number(value)
                if candidate:
                    result.fields.setdefault("documentNumber", candidate)
            elif field_name == "sex":
                upper = norm(value)
                if upper.startswith(("M", "Э", "E")):
                    result.fields.setdefault("sex", "M")
                elif upper.startswith(("F", "A", "Ж")):
                    result.fields.setdefault("sex", "F")
            elif field_name == "nationality":
                upper = norm(value).replace(" ", "")
                if 2 <= len(upper) <= 12:
                    result.fields.setdefault("nationality", upper)

    # 3) Zaxira: sana yorlig'i o'qilmagan bo'lsa ham, matnda bitta sana bo'lsa
    if "birthDate" not in result.fields:
        dates = [parse_date(region.text) for region in regions]
        found = sorted({d for d in dates if d})
        if len(found) == 1:
            result.fields["birthDate"] = found[0]

    return result
