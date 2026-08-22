"""Hujjatni tekshirish: ikki mustaqil manbani bir-biriga qarshi qo'yish.

Bitta OCR natijasi qanchalik yaxshi bo'lmasin, u faqat "shunday o'qidim"
degani. Hujjat ROSTDAN ham to'g'ri o'qilganini ko'rsatadigan narsa —
bir-biridan mustaqil ikkita manbaning bir xil javob berishi:

  * MRZ — nazorat raqamlari bilan matematik tekshiriladi;
  * bosma tomon — butunlay boshqa shrift, boshqa joylashuv, boshqa OCR o'tishi;
  * JSHSHIR — o'z ichida tug'ilgan sana va jinsni saqlaydi;
  * ID kartaning ikki tomoni — bir hujjatga tegishli ekani tekshiriladi.

Shuning uchun bu yerdagi har tekshiruv NOMLI va natijasi foydalanuvchiga
ko'rsatiladi: qabulxona xodimi nima tasdiqlangani va nima shubhali ekanini
ko'rib turadi, "ishonavering" degan yagona bayroqqa qaramaydi.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import visual

#: Tekshiruv holati. `fail` — hujjat tasdiqlanmaydi; `warn` — e'tibor talab
#: qiladi, lekin o'z-o'zidan hujjatni rad etmaydi.
OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass
class Check:
    key: str
    label: str
    status: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "status": self.status, "detail": self.detail}


@dataclass
class Verification:
    checks: list[Check] = field(default_factory=list)
    #: Maydon -> qaysi manbalar shu qiymatni bergani
    agreement: dict[str, list[str]] = field(default_factory=dict)

    def add(self, key: str, label: str, status: str, detail: str = "") -> None:
        self.checks.append(Check(key, label, status, detail))

    @property
    def failed(self) -> list[Check]:
        return [check for check in self.checks if check.status == FAIL]

    @property
    def warned(self) -> list[Check]:
        return [check for check in self.checks if check.status == WARN]

    def as_list(self) -> list[dict]:
        return [check.as_dict() for check in self.checks]


def _norm_name(value: str | None) -> str:
    return visual.norm(value or "").replace(" ", "")


def _norm_number(value: str | None) -> str:
    return (value or "").upper().replace(" ", "").replace("-", "")


#: Maydon nomi -> foydalanuvchiga ko'rsatiladigan nom
FIELD_LABELS = {
    "documentNumber": "Hujjat raqami",
    "personalNumber": "JSHSHIR",
    "birthDate": "Tug‘ilgan sana",
    "lastName": "Familiya",
    "firstName": "Ism",
}

#: Ikki manba solishtiriladigan maydonlar va ular qanday normallashtirilishi
_COMPARABLE = {
    "documentNumber": _norm_number,
    "personalNumber": _norm_number,
    "birthDate": lambda value: (value or "").strip(),
    "lastName": _norm_name,
    "firstName": _norm_name,
}


def cross_check(
    mrz_fields: dict,
    visual_fields: dict,
    *,
    mrz_label: str = "MRZ",
    visual_label: str = "bosma tomon",
) -> tuple[Verification, dict]:
    """Ikki manbani solishtiradi va har maydon uchun yakuniy qiymat tanlaydi.

    Qaytaradi: (tekshiruvlar, maydonlar). Maydonlarda MRZ ustun turadi —
    uning nazorat raqamlari bor — lekin qarama-qarshilik yashirilmaydi.
    """
    verification = Verification()
    resolved: dict = {}

    for name, normalize in _COMPARABLE.items():
        from_mrz = mrz_fields.get(name)
        from_visual = visual_fields.get(name)
        label = FIELD_LABELS[name]
        if from_mrz and from_visual:
            if normalize(from_mrz) == normalize(from_visual):
                verification.add(
                    f"agree.{name}", f"{label}: ikki manba mos", OK,
                    f"{mrz_label} va {visual_label} bir xil: {from_mrz}",
                )
                verification.agreement[name] = [mrz_label, visual_label]
            else:
                verification.add(
                    f"agree.{name}", f"{label}: manbalar mos kelmadi", FAIL,
                    f"{mrz_label}: {from_mrz} · {visual_label}: {from_visual}",
                )
                verification.agreement[name] = [mrz_label]
            resolved[name] = from_mrz
        elif from_mrz:
            resolved[name] = from_mrz
            verification.agreement[name] = [mrz_label]
        elif from_visual:
            resolved[name] = from_visual
            verification.agreement[name] = [visual_label]

    # Solishtirilmaydigan, lekin natijaga kiradigan maydonlar
    for name in ("patronymic", "nationality", "issuingCountry", "expiryDate", "sex"):
        value = mrz_fields.get(name) or visual_fields.get(name)
        if value:
            resolved[name] = value

    return verification, resolved


def check_pinfl(verification: Verification, fields: dict) -> bool:
    """JSHSHIR tuzilishi va uning ichidagi sana/jins hujjatga mos kelishini.

    JSHSHIR o'zi ichida tug'ilgan sanani saqlaydi, ya'ni u MRZ'dagi sanani
    MUSTAQIL tasdiqlaydigan uchinchi manba. Ular mos kelmasa — biri xato
    o'qilgan yoki hujjat soxta.
    """
    pinfl = fields.get("personalNumber")
    if not pinfl:
        return False
    if not visual.valid_pinfl(pinfl):
        verification.add(
            "pinfl.structure", "JSHSHIR tuzilishi", FAIL,
            f"{pinfl} — JSHSHIR shakliga mos kelmadi",
        )
        fields.pop("personalNumber", None)
        return False

    verification.add("pinfl.structure", "JSHSHIR tuzilishi to‘g‘ri", OK, pinfl)
    embedded = visual.pinfl_birth_date(pinfl)
    birth = fields.get("birthDate")
    if embedded and birth:
        if embedded == birth:
            verification.add(
                "pinfl.birth", "JSHSHIR ichidagi sana tug‘ilgan sanaga mos", OK, embedded
            )
        else:
            verification.add(
                "pinfl.birth", "JSHSHIR ichidagi sana mos kelmadi", FAIL,
                f"JSHSHIR: {embedded} · hujjat: {birth}",
            )
    elif embedded and not birth:
        fields["birthDate"] = embedded
        verification.add(
            "pinfl.birth", "Tug‘ilgan sana JSHSHIR'dan olindi", WARN, embedded
        )

    # Birinchi raqam jinsni ham kodlaydi: toq — erkak, juft — ayol
    sex = fields.get("sex")
    if sex in ("M", "F"):
        expected = "M" if int(pinfl[0]) % 2 else "F"
        if expected == sex:
            verification.add("pinfl.sex", "JSHSHIR jinsga mos", OK)
        else:
            verification.add(
                "pinfl.sex", "JSHSHIR jins bilan mos kelmadi", FAIL,
                f"JSHSHIR: {expected} · hujjat: {sex}",
            )
    return True


def check_dates(verification: Verification, fields: dict, today: date | None = None) -> None:
    """Sanalar mantiqan mumkinmi va hujjat amal qiladimi."""
    today = today or date.today()
    birth = fields.get("birthDate")
    expiry = fields.get("expiryDate")

    if birth:
        try:
            birth_date = date.fromisoformat(birth)
        except ValueError:
            verification.add("date.birth", "Tug‘ilgan sana yaroqsiz", FAIL, birth)
            fields.pop("birthDate", None)
            birth_date = None
        else:
            age = (today - birth_date).days / 365.25
            if birth_date > today:
                verification.add(
                    "date.birth", "Tug‘ilgan sana kelajakda", FAIL, birth
                )
            elif age > 120:
                verification.add(
                    "date.birth", "Tug‘ilgan sana haqiqiy emas", FAIL, f"{birth} ({int(age)} yosh)"
                )
            else:
                verification.add("date.birth", "Tug‘ilgan sana mumkin", OK, f"{birth} · {int(age)} yosh")
    else:
        verification.add("date.birth", "Tug‘ilgan sana topilmadi", WARN)

    if expiry:
        try:
            expiry_date = date.fromisoformat(expiry)
        except ValueError:
            verification.add("date.expiry", "Amal qilish muddati yaroqsiz", WARN, expiry)
            return
        if expiry_date < today:
            verification.add(
                "date.expiry", "Hujjat muddati tugagan", FAIL,
                f"{expiry} — mehmonni ro‘yxatga olishdan oldin tekshiring",
            )
        elif (expiry_date - today).days <= 30:
            verification.add(
                "date.expiry", "Hujjat muddati tugay deb qolgan", WARN, expiry
            )
        else:
            verification.add("date.expiry", "Hujjat amal qiladi", OK, expiry)


def check_document_number(verification: Verification, fields: dict) -> None:
    """O'zbek hujjat raqami shakli: ikki lotin harfi va yetti raqam."""
    number = fields.get("documentNumber")
    if not number:
        verification.add("number.present", "Hujjat raqami topilmadi", WARN)
        return
    country = fields.get("issuingCountry") or fields.get("nationality")
    compact = _norm_number(number)
    if country == "UZB":
        import re

        if re.fullmatch(r"[A-Z]{2}\d{7}", compact):
            verification.add("number.shape", "Hujjat raqami shakli to‘g‘ri", OK, compact)
        else:
            verification.add(
                "number.shape", "Hujjat raqami odatdagi shaklda emas", WARN,
                f"{compact} — O‘zbekiston hujjatlarida AA1234567 ko‘rinishida bo‘ladi",
            )
    else:
        verification.add("number.shape", "Hujjat raqami olindi", OK, compact)


def check_sides_match(
    verification: Verification, front: dict, back: dict
) -> None:
    """ID kartaning ikki tomoni bitta hujjatga tegishli ekanini.

    Qabulxonada ikki xil kartaning tomonlari adashib ketishi mumkin — masalan
    ikki mehmon navbatda turganda. Bu tekshiruvsiz natija bir odamning ismi va
    boshqasining raqami bo'lib chiqadi.
    """
    compared = 0
    for name in ("documentNumber", "personalNumber"):
        front_value = front.get(name)
        back_value = back.get(name)
        if not front_value or not back_value:
            continue
        compared += 1
        if _norm_number(front_value) == _norm_number(back_value):
            verification.add(
                f"sides.{name}", f"{FIELD_LABELS[name]} ikki tomonda bir xil", OK, front_value
            )
        else:
            verification.add(
                f"sides.{name}", "Ikki tomon boshqa hujjatga tegishli", FAIL,
                f"old: {front_value} · orqa: {back_value}",
            )
    if not compared:
        verification.add(
            "sides.link", "Tomonlarni bog‘lab bo‘lmadi", WARN,
            "Old va orqa tomonda umumiy raqam topilmadi",
        )
