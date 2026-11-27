"""E-mehmon (emehmon.uz) CRM API — sof qoidalar: sozlama, imzo, so'rov tanasi.

E-mehmon — mehmonlarni davlat ro'yxatiga olish tizimi. Mehmonxona unga
"listok" (ro'yxat varaqasi) yuboradi: mehmon kirganda — yaratadi, chiqqanda —
yopadi (checkout). Integratsiya IXTIYORIY va har FILIAL uchun alohida:
sozlama `branches.settings["emehmon"]` da, uni faqat sozlovchi / tizim ma'muri
kiritadi. Sozlama yoqilmagan filialda hech narsa o'zgarmaydi.

Bu faylda tarmoq ham, baza ham yo'q — faqat hisob-kitob:

* sozlamani to'ldirish va tekshirish (`resolve`, `validate`, `public`);
* so'rov imzosi: `HMAC_SHA256(X-User-Name + X-User-Ts + X-User-Request-Id)`;
* shakl → `POST /listok` tanasi (`build_listok`) va uning tekshiruvi;
* E-mehmon javobini o'qish (`parse_envelope`) va xato kodlarining o'zbekcha
  izohi (`error_text`).

Muhim qoidalar (hujjat: sandbox-api.emehmon.uz/docs):

* `datevisiton` — HAQIQIY kirish vaqti, Toshkent vaqti bilan (`+05:00`),
  faqat oxirgi 12 soat ichida va kelajakda emas.
* Ism-familiya — lotin harflarida, pasportdagidek.
* O'zbekiston fuqarosi (173) va fuqaroligi yo'q shaxs (231) — "mahalliy":
  PINFL kerak (yubormasak E-mehmon o'zi pasport + tug'ilgan sana bo'yicha
  IIV bazasidan topadi). Qolganlar — chet ellik: `id_person` majburiy, u
  `person-lookup` (Chegara xizmati bazasi) orqali olinadi.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone

SETTINGS_KEY = "emehmon"

ENV_SANDBOX = "sandbox"
ENV_PRODUCTION = "production"
#: Manzil faqat shu ikkitadan biri — erkin manzil kiritilmaydi (server o'zi
#: so'rov yuboradi, shuning uchun begona manzilga yo'l yo'q)
BASE_URLS = {
    ENV_SANDBOX: "https://sandbox-api.emehmon.uz",
    ENV_PRODUCTION: "https://api.emehmon.uz",
}
API_PREFIX = "/sync/v1/crm"

UZBEKISTAN = 173
STATELESS = 231
#: "Mahalliy" fuqaroliklar: PINFL bo'yicha yuradi
LOCAL_CITIZENS = (UZBEKISTAN, STATELESS)
PASSPORT_TYPE_PASSPORT = 1
#: Harbiy guvohnoma — mahalliy fuqarodan PINFL talab qilinmaydi
PASSPORT_TYPE_MILITARY = 2

TASHKENT = timezone(timedelta(hours=5))
#: E-mehmon kirish vaqtini faqat shu oraliqda qabul qiladi
ARRIVAL_WINDOW = timedelta(hours=12)

SEXES = ("M", "W")
#: 1 boshqa · 2 naqd · 3 Humo · 4 Uzcard · 5 o'tkazma · 6 shartnoma
PAYMENT_TYPES = (1, 2, 3, 4, 5, 6)
PAYMENT_OTHER, PAYMENT_CASH, PAYMENT_HUMO, PAYMENT_UZCARD, PAYMENT_TRANSFER, PAYMENT_CONTRACT = PAYMENT_TYPES
CARD_PAYMENT_TYPES = (PAYMENT_HUMO, PAYMENT_UZCARD)

DICTIONARIES = (
    "countries", "regions", "districts", "room-types", "visit-types",
    "guest-types", "passport-types", "visa-types", "payment-types",
)
LANGUAGES = ("uz", "ru", "en")

#: Bazada saqlanadigan, lekin javobda HECH QACHON qaytmaydigan maydonlar
SECRET_FIELDS = ("secret_enc",)

_SPACES = re.compile(r"\s+")
_DIGITS = re.compile(r"\D+")
_NAME_JUNK = re.compile(r"[^A-Z '\-]+")

#: Mehmon kartasidagi fuqarolik (o'zbekcha nom) → ISO kodi. E-mehmon
#: `countries` ma'lumotnomasida shu kod bor — id shu orqali topiladi.
NATIONALITY_CODES = {
    "o'zbekiston": "UZB", "qozog'iston": "KAZ", "qirg'iziston": "KGZ", "tojikiston": "TJK",
    "turkmaniston": "TKM", "rossiya": "RUS", "afg'oniston": "AFG", "ozarbayjon": "AZE",
    "armaniston": "ARM", "belarus": "BLR", "gruziya": "GEO", "turkiya": "TUR", "xitoy": "CHN",
    "hindiston": "IND", "pokiston": "PAK", "eron": "IRN", "janubiy koreya": "KOR",
    "yaponiya": "JPN", "malayziya": "MYS", "indoneziya": "IDN", "baa": "ARE",
    "saudiya arabistoni": "SAU", "misr": "EGY", "isroil": "ISR", "aqsh": "USA", "kanada": "CAN",
    "buyuk britaniya": "GBR", "germaniya": "DEU", "fransiya": "FRA", "italiya": "ITA",
    "ispaniya": "ESP", "niderlandiya": "NLD", "belgiya": "BEL", "shveytsariya": "CHE",
    "avstriya": "AUT", "polsha": "POL", "chexiya": "CZE", "ukraina": "UKR", "moldova": "MDA",
    "litva": "LTU", "latviya": "LVA", "estoniya": "EST", "shvetsiya": "SWE", "norvegiya": "NOR",
    "finlyandiya": "FIN", "daniya": "DNK", "avstraliya": "AUS", "braziliya": "BRA",
}

_CYRILLIC = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "YO", "Ж": "J", "З": "Z",
    "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M", "Н": "N", "О": "O", "П": "P", "Р": "R",
    "С": "S", "Т": "T", "У": "U", "Ф": "F", "Х": "X", "Ц": "TS", "Ч": "CH", "Ш": "SH",
    "Щ": "SH", "Ъ": "", "Ы": "I", "Ь": "", "Э": "E", "Ю": "YU", "Я": "YA",
    "Ў": "O'", "Қ": "Q", "Ғ": "G'", "Ҳ": "H",
}
_APOSTROPHES = str.maketrans({"ʻ": "'", "ʼ": "'", "’": "'", "‘": "'", "`": "'"})


# ------------------------------------------------------------ sozlama --


def _text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def resolve(raw: dict | None) -> dict:
    """Saqlangan sozlama → to'liq sozlama (yetishmaganlari standart bilan)."""
    raw = raw if isinstance(raw, dict) else {}
    environment = raw.get("environment")
    if environment not in BASE_URLS:
        environment = ENV_SANDBOX
    card = _int(raw.get("card_payment_type_id"))
    secret_enc = raw.get("secret_enc") or None
    return {
        "enabled": bool(raw.get("enabled")),
        "environment": environment,
        "username": _text(raw.get("username"), 120),
        "secret_enc": secret_enc,
        "secret_set": bool(secret_enc),
        "secret_hint": _text(raw.get("secret_hint"), 8) if secret_enc else "",
        "default_visit_type_id": _int(raw.get("default_visit_type_id")),
        "default_guest_type_id": _int(raw.get("default_guest_type_id")),
        "card_payment_type_id": card if card in CARD_PAYMENT_TYPES else PAYMENT_UZCARD,
        # Mehmon bizda chiqarilganda E-mehmon'dan ham avtomatik chiqariladi
        "auto_checkout": raw.get("auto_checkout", True) is not False,
        # Kirish rasmiylashtirilgach ro'yxatga olish oynasi o'zi ochiladi
        "prompt_on_checkin": raw.get("prompt_on_checkin", True) is not False,
        "enabled_at": raw.get("enabled_at") or None,
    }


def validate(cfg: dict) -> list[str]:
    """Yoqish uchun yetishmayotgan narsalar (bo'sh ro'yxat — hammasi joyida)."""
    problems: list[str] = []
    if not cfg.get("username"):
        problems.append("E-mehmon login (X-User-Name) kiritilmagan")
    if not cfg.get("secret_set"):
        problems.append("E-mehmon maxfiy kaliti (secret) kiritilmagan")
    return problems


def public(cfg: dict) -> dict:
    """Javobga chiqadigan qism — maxfiy kalitsiz."""
    data = {k: v for k, v in cfg.items() if k not in SECRET_FIELDS}
    data["base_url"] = BASE_URLS[cfg["environment"]]
    return data


def base_url(cfg: dict) -> str:
    return BASE_URLS[cfg["environment"]]


def enabled_since(cfg: dict) -> datetime | None:
    """Integratsiya yoqilgan payt — bundan oldin kirgan mehmonlar navbatga
    tushmaydi (yoqilganda eski turishlar to'satdan "ro'yxatga olinmagan"
    bo'lib chiqmasin)."""
    raw = cfg.get("enabled_at")
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# -------------------------------------------------------------- imzo --


def sign(username: str, ts: str, request_id: str, secret: str) -> str:
    """`X-User-Hash`: HMAC-SHA256(login + vaqt + so'rov id), kichik hex."""
    message = f"{username}{ts}{request_id}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def auth_headers(
    username: str, secret: str, *, now: float | None = None, request_id: str | None = None
) -> dict[str, str]:
    """Har so'rovga YANGI `X-User-Request-Id` (takrori — 403 REPLAY_ATTACK)."""
    ts = str(int(now if now is not None else time.time()))
    rid = request_id or str(uuid.uuid4())
    return {
        "X-User-Name": username,
        "X-User-Ts": ts,
        "X-User-Request-Id": rid,
        "X-User-Hash": sign(username, ts, rid, secret),
    }


# ---------------------------------------------------- qiymatlarni tozalash --


def normalize_passport(value) -> str:
    """Pasport seriya va raqami: bo'shliqsiz, katta harflarda."""
    return _SPACES.sub("", str(value or "")).replace("-", "").upper()[:50]


def latin_name(value) -> str:
    """Ism / familiya — lotin katta harflarida (kirill bo'lsa o'giriladi).

    O'girish — faqat DASTLABKI to'ldirish: aniq yozilish pasportdagidek
    bo'lishi kerak, xodim uni tekshiradi yoki «Bazadan tekshirish» rasmiy
    yozilishni olib keladi.
    """
    text = str(value or "").translate(_APOSTROPHES).upper()
    text = "".join(_CYRILLIC.get(ch, ch) for ch in text)
    text = _NAME_JUNK.sub(" ", text)
    return _SPACES.sub(" ", text).strip()[:50]


def digits(value, limit: int = 20) -> str:
    return _DIGITS.sub("", str(value or ""))[:limit]


def iso_date(value) -> str | None:
    """`YYYY-MM-DD` yoki `None` (noto'g'ri sana qabul qilinmaydi)."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def country_code(nationality: str | None) -> str | None:
    """Mehmon kartasidagi fuqarolik matni → ISO kodi (topilmasa `None`)."""
    key = str(nationality or "").translate(_APOSTROPHES).strip().lower()
    if not key:
        return None
    if key in NATIONALITY_CODES:
        return NATIONALITY_CODES[key]
    upper = key.upper()
    return upper if len(upper) == 3 and upper.isalpha() else None


def citizen_id_for(nationality: str | None, countries: list[dict] | None) -> int | None:
    """Fuqarolik matni → E-mehmon `countries` ma'lumotnomasidagi id."""
    code = country_code(nationality)
    if code == "UZB":
        return UZBEKISTAN
    if not code:
        return None
    for item in countries or []:
        if str(item.get("code") or "").upper() == code:
            return _int(item.get("id"))
    return None


def is_local(citizen_id) -> bool:
    return _int(citizen_id) in LOCAL_CITIZENS


# ------------------------------------------------------- kirish vaqti --


def tashkent_iso(moment: datetime) -> str:
    """`2026-05-22T14:30:00+05:00` — E-mehmon soat va daqiqani QANDAY kelsa
    SHUNDAY yozadi, shuning uchun vaqt Toshkent vaqtida yuboriladi."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(TASHKENT).replace(microsecond=0).isoformat()


def parse_moment(value) -> datetime | None:
    """ISO vaqt → aniq (tz bilan) vaqt. Zonasiz qiymat Toshkent vaqti deb olinadi."""
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TASHKENT)
    return moment


def arrival_window(now: datetime) -> tuple[datetime, datetime]:
    return now - ARRIVAL_WINDOW, now


def arrival_problem(arrival: datetime | None, now: datetime) -> str | None:
    """Kirish vaqti E-mehmon qabul qiladigan oraliqdami."""
    if arrival is None:
        return "Kirish vaqti ko'rsatilmagan"
    earliest, latest = arrival_window(now)
    # Soatlar orasidagi kichik farq (brauzer ↔ server) uchun bir daqiqa
    if arrival > latest + timedelta(minutes=1):
        return "Kirish vaqti kelajakda bo'lishi mumkin emas"
    if arrival < earliest:
        return "Kirish vaqti 12 soatdan eski — E-mehmon faqat oxirgi 12 soatni qabul qiladi"
    return None


def stay_days(check_in: date | None, check_out: date | None) -> int:
    """Rejalashtirilgan sutkalar soni (1..365)."""
    if not check_in or not check_out:
        return 1
    return max(1, min(365, (check_out - check_in).days))


# ------------------------------------------------------ listok tanasi --


def clean_form(form: dict | None) -> dict:
    """Shakl qiymatlari — tozalangan, bir xil ko'rinishda (saqlash va
    qayta to'ldirish uchun ham shu ishlatiladi)."""
    form = form if isinstance(form, dict) else {}
    children = []
    for child in form.get("children") or []:
        if not isinstance(child, dict):
            continue
        name = _text(child.get("full_name"), 255)
        if not name:
            continue
        children.append({
            "full_name": name,
            "gen": child.get("gen") if child.get("gen") in SEXES else None,
            "birth_day": iso_date(child.get("birth_day")),
        })
    arrival = parse_moment(form.get("arrival"))
    amount = form.get("amount")
    try:
        amount = max(0, round(float(amount))) if amount not in (None, "") else None
    except (TypeError, ValueError):
        amount = None
    days = _int(form.get("days"))
    return {
        "surname": latin_name(form.get("surname")),
        "firstname": latin_name(form.get("firstname")),
        "lastname": latin_name(form.get("lastname")),
        "citizen_id": _int(form.get("citizen_id")),
        "passport_number": normalize_passport(form.get("passport_number")),
        "passport_type_id": _int(form.get("passport_type_id")) or PASSPORT_TYPE_PASSPORT,
        "passport_date": iso_date(form.get("passport_date")),
        "passport_issued_by": _text(form.get("passport_issued_by"), 255),
        "birth_date": iso_date(form.get("birth_date")),
        "sex": form.get("sex") if form.get("sex") in SEXES else None,
        "pinfl": digits(form.get("pinfl"), 14),
        "person_id": _text(form.get("person_id"), 255),
        "visit_type_id": _int(form.get("visit_type_id")),
        "guest_type_id": _int(form.get("guest_type_id")),
        "arrival": tashkent_iso(arrival) if arrival else None,
        "days": min(days, 365) if days else None,
        "room": _text(form.get("room"), 50),
        "amount": amount,
        "paid": bool(form.get("paid")),
        "birth_country_id": _int(form.get("birth_country_id")),
        "from_country_id": _int(form.get("from_country_id")),
        "visa_type_id": _int(form.get("visa_type_id")),
        "visa_number": _text(form.get("visa_number"), 50),
        "visa_from": iso_date(form.get("visa_from")),
        "visa_to": iso_date(form.get("visa_to")),
        "visa_issued_by": _text(form.get("visa_issued_by"), 255),
        "kpp_number": _text(form.get("kpp_number"), 50),
        "kpp_date": iso_date(form.get("kpp_date")),
        "children": children,
    }


def form_problems(form: dict, now: datetime) -> list[str]:
    """Yuborishdan OLDINGI tekshiruv (`form` — `clean_form` natijasi).

    E-mehmon baribir o'zi tekshiradi; bu yerda ochiq-oydin xatolar tarmoqqa
    chiqmasdan, tushunarli matn bilan qaytariladi.
    """
    problems: list[str] = []
    if not form["surname"]:
        problems.append("Familiya (lotin harflarida) kiritilmagan")
    if not form["firstname"]:
        problems.append("Ism (lotin harflarida) kiritilmagan")
    if not form["citizen_id"]:
        problems.append("Fuqarolik tanlanmagan")
    if not form["passport_number"]:
        problems.append("Pasport seriya va raqami kiritilmagan")
    if not form["birth_date"]:
        problems.append("Tug'ilgan sana kiritilmagan")
    if not form["passport_date"]:
        problems.append("Pasport berilgan sana kiritilmagan")
    elif form["birth_date"] and form["passport_date"] <= form["birth_date"]:
        problems.append("Pasport berilgan sana tug'ilgan sanadan keyin bo'lishi kerak")
    if not form["sex"]:
        problems.append("Jinsi tanlanmagan")
    if not form["visit_type_id"]:
        problems.append("Tashrif maqsadi tanlanmagan")
    if not form["guest_type_id"]:
        problems.append("Mehmon turi tanlanmagan")
    if not form["days"]:
        problems.append("Turish muddati (sutka) kiritilmagan")
    arrival_issue = arrival_problem(parse_moment(form["arrival"]), now)
    if arrival_issue:
        problems.append(arrival_issue)
    if form["citizen_id"] and not is_local(form["citizen_id"]) and not form["person_id"]:
        problems.append(
            "Chet el fuqarosi uchun «Bazadan tekshirish» kerak (Chegara xizmati identifikatori)"
        )
    if form["pinfl"] and len(form["pinfl"]) != 14:
        problems.append("PINFL 14 ta raqamdan iborat bo'lishi kerak")
    return problems


def build_listok(form: dict, *, actor_id: str | None = None, actor_name: str | None = None) -> dict:
    """`clean_form` natijasi → `POST /listok` tanasi (bo'sh maydonlarsiz)."""
    body: dict = {
        "surname": form["surname"],
        "firstname": form["firstname"],
        "id_citizen": form["citizen_id"],
        "passportNumber": form["passport_number"],
        "datePassport": form["passport_date"],
        "datebirth": form["birth_date"],
        "datevisiton": form["arrival"],
        "wdays": form["days"],
        "sex": form["sex"],
        "id_visittype": form["visit_type_id"],
        "id_guest": form["guest_type_id"],
    }
    optional = {
        "lastname": form["lastname"],
        "id_passporttype": form["passport_type_id"]
        if form["passport_type_id"] != PASSPORT_TYPE_PASSPORT else None,
        "passportIssuedBy": form["passport_issued_by"],
        "propiska": form["room"],
        "amount": form["amount"],
        "payed": 1 if form["paid"] else None,
        "id_country": form["birth_country_id"],
        "id_countryFrom": form["from_country_id"],
        "id_visatype": form["visa_type_id"],
        "visaNumber": form["visa_number"],
        "dateVisaOn": form["visa_from"],
        "dateVisaOff": form["visa_to"],
        "visaIssuedBy": form["visa_issued_by"],
        "kppNumber": form["kpp_number"],
        "dateKPP": form["kpp_date"],
        "crm_actor_id": _text(actor_id, 64),
        "crm_actor_name": _text(actor_name, 120),
    }
    if is_local(form["citizen_id"]):
        # PINFL bo'lmasa yuborilmaydi — E-mehmon pasport + tug'ilgan sana
        # bo'yicha IIV bazasidan o'zi topadi
        optional["pinfl"] = form["pinfl"]
    else:
        optional["id_person"] = form["person_id"]
    for key, value in optional.items():
        if value not in (None, "", []):
            body[key] = value
    if form["children"]:
        body["children"] = form["children"]
    return body


def encode_body(body: dict) -> bytes:
    """Tana — DOIM bir xil baytlar: `Idempotency-Key` bilan qayta yuborishda
    tana aynan bir xil bo'lishi shart."""
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def body_hash(body: dict) -> str:
    return hashlib.sha256(encode_body(body)).hexdigest()


# ------------------------------------------------------------ chiqish --

_LEGACY_METHODS = {
    "CREDIT_CARD": "CARD",
    "DEBIT_CARD": "CARD",
    "MOBILE_PAYMENT": "ONLINE",
    "TRANSFER": "BANK_TRANSFER",
}


def payment_type_for(method: str | None, cfg: dict) -> int:
    """Bizdagi to'lov usuli → E-mehmon `payment_type_id`."""
    key = str(method or "").strip().upper()
    key = _LEGACY_METHODS.get(key, key)
    if key == "CASH":
        return PAYMENT_CASH
    if key == "CARD":
        return cfg.get("card_payment_type_id") or PAYMENT_UZCARD
    if key == "BANK_TRANSFER":
        return PAYMENT_TRANSFER
    return PAYMENT_OTHER


def dominant_method(amounts: dict[str, float]) -> str | None:
    """Eng ko'p pul tushgan to'lov usuli (to'lov bo'lmasa `None`)."""
    best = None
    for method, amount in amounts.items():
        if amount > 0 and (best is None or amount > amounts[best]):
            best = method
    return best


def checkout_share(total: float, reported: float, remaining: int) -> int:
    """Bron summasidan shu mehmonga to'g'ri keladigan ulush (butun so'm).

    Bron bitta, mehmon (listok) bir nechta bo'lishi mumkin: summa hali
    chiqarilmagan mehmonlar o'rtasida teng bo'linadi — hammasi chiqarilganda
    yig'indi bron summasiga teng chiqadi.
    """
    rest = max(float(total or 0) - float(reported or 0), 0.0)
    return int(round(rest / max(int(remaining or 1), 1)))


# ------------------------------------------------------------- javob --

NETWORK_ERROR = "NETWORK_ERROR"
BAD_RESPONSE = "BAD_RESPONSE"

#: Natijasi NOMA'LUM xatolar: so'rov E-mehmon'ga yetgan bo'lishi mumkin —
#: qayta yuborishda o'sha `Idempotency-Key` ishlatiladi (ikki marta yozilmasin)
UNCERTAIN_CODES = frozenset({
    NETWORK_ERROR, BAD_RESPONSE, "INTERNAL_ERROR", "INTERNAL_GATEWAY_ERROR",
    "SERVICE_UNAVAILABLE", "TOO_MANY_REQUESTS",
})

ERROR_TEXTS = {
    NETWORK_ERROR: "E-mehmon serveriga ulanib bo'lmadi. Birozdan keyin qayta urinib ko'ring",
    BAD_RESPONSE: "E-mehmon tushunarsiz javob qaytardi. Birozdan keyin qayta urinib ko'ring",
    "MISSING_AUTH_HEADERS": "So'rovda imzo sarlavhalari yetishmaydi",
    "INVALID_TIMESTAMP": "So'rov vaqti noto'g'ri",
    "REQUEST_TIME_EXPIRED": "Server soati E-mehmon soatidan 5 daqiqadan ko'p farq qilmoqda",
    "INVALID_REQUEST_ID": "So'rov identifikatori noto'g'ri",
    "USER_NOT_FOUND": "E-mehmon login topilmadi — Sozlamalardagi loginni tekshiring",
    "IP_DENY": "Server IP manzili E-mehmon'da ruxsat etilmagan — E-mehmon ma'muriga murojaat qiling",
    "REPLAY_ATTACK": "So'rov takrorlandi — qayta urinib ko'ring",
    "INVALID_SIGNATURE": "Imzo noto'g'ri — Sozlamalardagi maxfiy kalitni tekshiring",
    "ACCESS_DENIED": "E-mehmon bu amalga ruxsat bermadi",
    "HOTEL_MISMATCH": "Bu yozuv boshqa mehmonxonaga tegishli",
    "INVALID_IDEMPOTENCY_KEY": "Takrorlash kaliti noto'g'ri",
    "IDEMPOTENCY_KEY_REUSED_DIFFERENT_BODY": "Ma'lumot o'zgargan — qayta yuboring",
    "HOTEL_BANNED": "Mehmonxona E-mehmon'da bloklangan",
    "HOTEL_LICENSE_EXPIRED": "Mehmonxona litsenziyasi muddati tugagan",
    "HOTEL_DEPOSIT_NEGATIVE": "E-mehmon depoziti yetarli emas — balansni to'ldiring",
    "CRM_REGISTRATION_DISABLED": "E-mehmon'da CRM orqali ro'yxatga olish vaqtincha yopilgan",
    "PERSON_ID_REQUIRED_FOR_FOREIGNER":
        "Chet el fuqarosi uchun avval «Bazadan tekshirish» kerak (Chegara xizmati identifikatori)",
    "PINFL_REQUIRED_FOR_LOCAL":
        "PINFL kerak: pasport va tug'ilgan sana bo'yicha IIV bazasidan topilmadi",
    "PERSON_NOT_FOUND_IN_SGB":
        "Chegara xizmati bazasida topilmadi — pasport raqami, tug'ilgan sana va fuqarolikni tekshiring",
    "PERSON_NOT_FOUND_IN_MVD":
        "IIV bazasida topilmadi — pasport raqami va tug'ilgan sanani tekshiring",
    "LOOKUP_TEMPORARILY_BLOCKED":
        "Ketma-ket topilmagan qidiruvlar sabab «Bazadan tekshirish» vaqtincha yopilgan",
    "LISTOK_DUPLICATE_ACTIVE": "Bu pasport bilan mehmonxonada faol ro'yxat allaqachon bor",
    "PASSPORT_DATE_BEFORE_BIRTHDAY": "Pasport berilgan sana tug'ilgan sanadan keyin bo'lishi kerak",
    "CHECKIN_DATE_OUT_OF_WINDOW":
        "Kirish vaqti ruxsat etilgan oraliqda emas (faqat oxirgi 12 soat, kelajak emas)",
    "ROOM_CAPACITY_EXCEEDED": "Xonada (yoki mehmonxonada) bo'sh o'rin qolmagan",
    "LISTOK_GRACE_PERIOD_EXPIRED": "Ro'yxatni faqat yaratilgandan keyingi 5 daqiqa ichida tuzatish mumkin",
    "LISTOK_ALREADY_CHECKED_OUT": "Mehmon E-mehmon'dan allaqachon chiqarilgan",
    "LISTOK_NOT_FOUND": "E-mehmon'da bunday ro'yxat topilmadi",
    "VALIDATION_ERROR": "E-mehmon ma'lumotni qabul qilmadi",
    "TOO_MANY_REQUESTS": "So'rovlar soni chegaradan oshdi — birozdan keyin qayta urinib ko'ring",
    "INTERNAL_ERROR": "E-mehmon serverida xatolik — birozdan keyin qayta urinib ko'ring",
    "INTERNAL_GATEWAY_ERROR": "E-mehmon tashqi bazaga ulana olmadi — birozdan keyin qayta urinib ko'ring",
    "SERVICE_UNAVAILABLE": "E-mehmon vaqtincha ishlamayapti — birozdan keyin qayta urinib ko'ring",
}

_ROOM_REASONS = {
    "room_full": "Xonadagi barcha o'rinlar band (E-mehmon bo'yicha)",
    "hotel_full": "Mehmonxonaning sertifikatlangan o'rinlari to'lgan",
}


def error_text(error_code: str | None, message: str | None = None, meta: dict | None = None) -> str:
    """E-mehmon xato kodi → xodim tushunadigan matn."""
    code = str(error_code or "")
    meta = meta if isinstance(meta, dict) else {}
    if code == "ROOM_CAPACITY_EXCEEDED" and meta.get("reason") in _ROOM_REASONS:
        return _ROOM_REASONS[meta["reason"]]
    text = ERROR_TEXTS.get(code)
    if text is None:
        return _text(message, 300) or "E-mehmon xatosi"
    if code == "VALIDATION_ERROR":
        field = _text(meta.get("field"), 60)
        detail = _text(message, 200)
        extra = " · ".join(part for part in (field, detail) if part)
        return f"{text}: {extra}" if extra else text
    if code == "CHECKIN_DATE_OUT_OF_WINDOW" and meta.get("min_allowed"):
        return f"{text}. Eng erta: {meta.get('min_allowed')}"
    return text


class EmehmonError(Exception):
    """E-mehmon so'rovi bajarilmadi (rad etildi yoki yetib bormadi)."""

    def __init__(
        self,
        error_code: str,
        message: str | None = None,
        *,
        status: int = 0,
        meta: dict | None = None,
        request_id: str | None = None,
    ) -> None:
        self.error_code = error_code or BAD_RESPONSE
        self.status = status
        self.meta = meta if isinstance(meta, dict) else {}
        self.request_id = request_id
        self.raw_message = message
        self.text = error_text(self.error_code, message, self.meta)
        super().__init__(self.text)

    @property
    def uncertain(self) -> bool:
        """Natija noma'lum: so'rov bajarilgan bo'lishi ham mumkin."""
        return self.error_code in UNCERTAIN_CODES

    def as_dict(self) -> dict:
        return {
            "error_code": self.error_code,
            "error": self.text,
            "meta": self.meta,
            "status": self.status,
        }


def parse_envelope(status: int, payload) -> dict:
    """E-mehmon javobi → `data`. Xato bo'lsa — `EmehmonError`.

    Muvaffaqiyat: `{"code": 200, "message": "OK", "data": {...}}`.
    Xato: `{"code": 422, "error_code": "...", "message": "...", "meta": {...}}` —
    matnga emas, `error_code` ga tayaniladi.
    """
    if not isinstance(payload, dict):
        raise EmehmonError(BAD_RESPONSE, status=status)
    if 200 <= status < 300 and not payload.get("error_code"):
        data = payload.get("data")
        return data if isinstance(data, dict) else {"value": data}
    raise EmehmonError(
        str(payload.get("error_code") or f"HTTP_{status}"),
        payload.get("message"),
        status=status,
        meta=payload.get("meta"),
        request_id=payload.get("request_id"),
    )


def person_from_lookup(data: dict | None) -> dict:
    """`person-lookup` javobi → shakl maydonlari (faqat kelganlari).

    Mahalliy fuqaro (IIV): pinfl, F.I.O., jins, pasport berilgan sana.
    Chet ellik (Chegara xizmati): `person_id` va viza maydonlari — javobning
    aniq tarkibi hujjatda to'liq berilmagan, shuning uchun bor maydonlargina
    olinadi.
    """
    data = data if isinstance(data, dict) else {}
    out: dict = {}

    def put(key: str, value) -> None:
        if value not in (None, "", []):
            out[key] = value

    put("surname", latin_name(data.get("surname")))
    put("firstname", latin_name(data.get("firstname")))
    put("lastname", latin_name(data.get("lastname")))
    put("birth_date", iso_date(data.get("datebirth")))
    put("sex", data.get("sex") if data.get("sex") in SEXES else None)
    put("passport_date", iso_date(data.get("datePassport")))
    put("passport_issued_by", _text(data.get("passportIssuedBy"), 255))
    put("pinfl", digits(data.get("pinfl"), 14))
    person_id = data.get("person_id") if data.get("person_id") is not None else data.get("id_person")
    put("person_id", _text(person_id, 255))
    put("visa_type_id", _int(data.get("id_visatype")))
    put("visa_number", _text(data.get("visaNumber"), 50))
    put("visa_from", iso_date(data.get("dateVisaOn")))
    put("visa_to", iso_date(data.get("dateVisaOff")))
    put("visa_issued_by", _text(data.get("visaIssuedBy"), 255))
    put("kpp_number", _text(data.get("kppNumber"), 50))
    put("kpp_date", iso_date(data.get("dateKPP")))
    put("birth_country_id", _int(data.get("id_country")))
    put("from_country_id", _int(data.get("id_countryFrom")))
    return out
