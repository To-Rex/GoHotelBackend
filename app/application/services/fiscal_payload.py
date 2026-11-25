"""Fiskal chek so'rovini tuzish (E-POS Communicator) — sof funksiyalar.

Sozlama filialda (`branches.settings["fiscal"]`), standartlari shu yerda.
Hujjat: E-POS "Universal Communicator" Postman kolleksiyasi (v3.23.6).

Qoidalar (E-POS talabi — kichik xato ham chekni rad etadi):
* Hamma summa TIYINDA (so'm × 100), butun son.
* `amount` — miqdor mingdan bir ulushda: 1000 = 1 dona.
* `price` — pozitsiyaning UMUMIY summasi (miqdor bilan).
* `vat` = (price − discount − other) × stavka / (100 + stavka); stavka 0 yoki 12.
* `receivedCash + receivedCard` = pozitsiyalar yig'indisi (chegirmalardan keyin).

Usullar: `fastSale` — chekni E-POS chop etmaydi (uni o'zimiz, soliq
havolasi QR bilan chiqaramiz); `sale` — E-POS o'zi chop etadi; `advance` —
oldindan to'lov; `refund` — qaytarim (asl chekning fiskal ma'lumoti bilan).
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

SETTINGS_KEY = "fiscal"

#: E-POS SINOV muhiti tokeni (hujjatdagi namuna). Standart sifatida
#: ISHLATILMAYDI: har kompaniya/filialning o'z tokeni bor, u sozlamada
#: kiritiladi va bazada shifrlangan holda saqlanadi (fiscal_crypto).
EPOS_TOKEN = "DXJFX32CN1296678504F2"
DEFAULT_URL = "http://localhost:8347/uzpos"

ITEM_KINDS = ("room", "service", "penalty", "shop")
ITEM_NAMES = {
    "room": "Mehmonxona xizmati (yashash)",
    "service": "Qo'shimcha xizmatlar",
    "penalty": "Jarima",
    "shop": "Do'kon mahsuloti",
}
#: To'lov usuli fiskal chekda qayerga tushadi: naqd, karta yoki umuman
#: fiskallashtirilmaydi (bank o'tkazmasi — hisob-faktura orqali yuradi)
METHOD_TARGETS = ("cash", "card", "skip")
DEFAULT_METHODS = {"CASH": "cash", "CARD": "card", "ONLINE": "card", "BANK_TRANSFER": "skip"}
_LEGACY_METHODS = {
    "CREDIT_CARD": "CARD",
    "DEBIT_CARD": "CARD",
    "MOBILE_PAYMENT": "ONLINE",
    "TRANSFER": "BANK_TRANSFER",
}
TRANSPORTS = ("browser", "server")
VAT_RATES = (0, 12)
PRINTER_SIZES = (58, 80)

_DIGITS = re.compile(r"\D+")


def canonical_method(code: str | None) -> str:
    key = str(code or "").strip().upper()
    return _LEGACY_METHODS.get(key, key)


def _text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _digits(value, limit: int) -> str:
    return _DIGITS.sub("", str(value or ""))[:limit]


def resolve(raw: dict | None) -> dict:
    """Saqlangan sozlama → to'liq sozlama (yetishmaganlari standart bilan)."""
    src = raw if isinstance(raw, dict) else {}
    items_src = src.get("items") if isinstance(src.get("items"), dict) else {}
    items = {}
    for kind in ITEM_KINDS:
        row = items_src.get(kind) if isinstance(items_src.get(kind), dict) else {}
        items[kind] = {
            "name": _text(row.get("name"), 120) or ITEM_NAMES[kind],
            "class_code": _digits(row.get("class_code"), 20),
            "package_code": _digits(row.get("package_code"), 20),
        }
    methods_src = src.get("methods") if isinstance(src.get("methods"), dict) else {}
    methods = dict(DEFAULT_METHODS)
    for code, target in methods_src.items():
        code = canonical_method(code)
        if code in DEFAULT_METHODS and target in METHOD_TARGETS:
            methods[code] = target
    try:
        vat = int(src.get("vat_percent") or 0)
    except (TypeError, ValueError):
        vat = 0
    try:
        size = int(src.get("printer_size") or 80)
    except (TypeError, ValueError):
        size = 80
    transport = src.get("transport") if src.get("transport") in TRANSPORTS else "browser"
    token_enc = _text(src.get("token_enc"), 4000)
    # Eski yozuv (shifrlanmagan) — keyingi saqlashda shifrlanadi
    token_plain = "" if token_enc else _text(src.get("token"), 300)
    return {
        "enabled": bool(src.get("enabled")),
        "transport": transport,
        "url": _text(src.get("url"), 300) or DEFAULT_URL,
        #: Shifrlangan token (ochilishi — fiscal_service.token_of)
        "token_enc": token_enc,
        "token_plain": token_plain,
        "token_hint": _text(src.get("token_hint"), 8) or token_plain[-4:],
        "token_set": bool(token_enc or token_plain),
        "company_name": _text(src.get("company_name"), 200),
        "company_address": _text(src.get("company_address"), 300),
        "company_inn": _digits(src.get("company_inn"), 14),
        "company_phone": _text(src.get("company_phone"), 30),
        "printer_size": size if size in PRINTER_SIZES else 80,
        "vat_percent": vat if vat in VAT_RATES else 0,
        "items": items,
        "methods": methods,
        "prepayment_as_advance": bool(src.get("prepayment_as_advance")),
        "auto_open_shift": src.get("auto_open_shift") is not False,
        "epos_prints": bool(src.get("epos_prints")),
        "enabled_at": src.get("enabled_at") or None,
    }


def validate(cfg: dict) -> list[str]:
    """Yoqish uchun yetishmayotgan narsalar (bo'sh ro'yxat — hammasi joyida)."""
    errors: list[str] = []
    if not cfg["token_set"]:
        errors.append("E-POS tokenini kiriting")
    if not cfg["company_name"]:
        errors.append("Kompaniya nomini kiriting")
    if not cfg["company_address"]:
        errors.append("Kompaniya manzilini kiriting")
    if len(cfg["company_inn"]) not in (9, 14):
        errors.append("STIR 9 xonali (yoki JSHSHIR 14 xonali) bo'lishi kerak")
    room = cfg["items"]["room"]
    if len(room["class_code"]) != 17:
        errors.append("Yashash xizmati uchun IKPU kodi 17 xonali bo'lishi kerak")
    if not room["package_code"]:
        errors.append("Yashash xizmati uchun o'ram kodini kiriting")
    for kind in ("service", "penalty", "shop"):
        row = cfg["items"][kind]
        if row["class_code"] and len(row["class_code"]) != 17:
            errors.append(f"«{row['name']}» uchun IKPU kodi 17 xonali bo'lishi kerak")
        if bool(row["class_code"]) != bool(row["package_code"]):
            errors.append(f"«{row['name']}» uchun IKPU va o'ram kodi birga kiritiladi")
    if not re.match(r"^https?://", cfg["url"]):
        errors.append("E-POS manzili http:// yoki https:// bilan boshlanishi kerak")
    return errors


def item_codes(cfg: dict, kind: str, own: tuple[str | None, str | None] = (None, None)) -> tuple[str, str]:
    """Pozitsiyaning IKPU va o'ram kodi: o'ziniki → tur standarti → yashash."""
    if own[0] and own[1]:
        return _digits(own[0], 20), _digits(own[1], 20)
    row = cfg["items"].get(kind) or {}
    if row.get("class_code") and row.get("package_code"):
        return row["class_code"], row["package_code"]
    room = cfg["items"]["room"]
    return room["class_code"], room["package_code"]


def to_tiyin(amount) -> int:
    return int((Decimal(str(amount or 0)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def vat_of(net_tiyin: int, percent: int) -> int:
    if not percent or net_tiyin <= 0:
        return 0
    return int(
        (Decimal(net_tiyin) * percent / (100 + percent)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def new_number() -> str:
    """Chek raqami (E-POS'da tashqi kalit): GH-XXXXXXXXXX."""
    return f"GH-{uuid.uuid4().hex[:10].upper()}"


def split_methods(payments: list[tuple[str | None, float]], cfg: dict) -> dict:
    """To'lovlar → `{cash, card, skip}` summalari (so'mda)."""
    out = {"cash": 0.0, "card": 0.0, "skip": 0.0}
    for method, amount in payments:
        target = cfg["methods"].get(canonical_method(method), "card")
        out[target] = round(out[target] + abs(float(amount or 0)), 2)
    return out


def make_item(name: str, price_tiyin: int, cfg: dict, codes: tuple[str, str], quantity: float = 1) -> dict:
    return {
        "name": _text(name, 250),
        "price": int(price_tiyin),
        "discount": 0,
        "other": 0,
        "barcode": "",
        "amount": int(round(float(quantity) * 1000)),
        "vatPercent": cfg["vat_percent"],
        "vat": vat_of(int(price_tiyin), cfg["vat_percent"]),
        "classCode": codes[0],
        "packageCode": codes[1],
        "label": "",
    }


def fit_total(items: list[dict], total_tiyin: int, cfg: dict) -> list[dict]:
    """Pozitsiyalar yig'indisini aynan `total_tiyin` ga keltiradi (yaxlitlash
    farqi oxirgi pozitsiyaga) — E-POS tengsizlikni rad etadi."""
    items = [i for i in items if i["price"] > 0]
    diff = total_tiyin - sum(i["price"] for i in items)
    if items and diff:
        last = items[-1]
        last["price"] = max(last["price"] + diff, 0)
        last["vat"] = vat_of(last["price"], cfg["vat_percent"])
    return [i for i in items if i["price"] > 0]


def reservation_items(parts: dict[str, float], total: float, cfg: dict, label: str = "") -> list[dict]:
    """Bron to'lovi pozitsiyalari: yashash / xizmat / jarima ulushlari bo'yicha.

    `parts` — `{room|service|penalty: so'm}`; yig'indisi `total` dan farq
    qilsa farq yashashga qo'shiladi.
    """
    suffix = f" — {label}" if label else ""
    items = []
    for kind in ("room", "service", "penalty"):
        value = float(parts.get(kind) or 0)
        if value > 0:
            items.append(
                make_item(cfg["items"][kind]["name"] + suffix, to_tiyin(value), cfg, item_codes(cfg, kind))
            )
    if not items:
        items.append(
            make_item(cfg["items"]["room"]["name"] + suffix, to_tiyin(total), cfg, item_codes(cfg, "room"))
        )
    # Farq yashash pozitsiyasida tuzatilishi uchun u oxirida turadi
    items.sort(key=lambda i: i["name"].startswith(cfg["items"]["room"]["name"]))
    return fit_total(items, to_tiyin(total), cfg)


def shop_items(rows: list[dict], paid: float, sale_total: float, cfg: dict) -> list[dict]:
    """Do'kon sotuvi pozitsiyalari. `rows`: `{name, quantity, total, ikpu, package}`.

    Sotuv qisman to'langan bo'lsa (bron hisobidagi qoldiq) har pozitsiya
    to'langan ulushga mutanosib kamayadi.
    """
    ratio = Decimal(str(paid)) / Decimal(str(sale_total)) if float(sale_total or 0) > 0 else Decimal(1)
    ratio = min(ratio, Decimal(1))
    items = []
    for row in rows:
        price = int(
            (Decimal(to_tiyin(row["total"])) * ratio).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        items.append(
            make_item(
                row["name"], price, cfg,
                item_codes(cfg, "shop", (row.get("ikpu"), row.get("package"))),
                quantity=row.get("quantity") or 1,
            )
        )
    return fit_total(items, to_tiyin(paid), cfg)


def refund_items(original_items: list[dict], refund_total: float, cfg: dict) -> list[dict]:
    """Qaytarim pozitsiyalari — asl chekdagilar, qaytariladigan summaga mutanosib."""
    base = sum(int(i.get("price") or 0) for i in original_items)
    want = to_tiyin(refund_total)
    if base <= 0:
        return []
    ratio = min(Decimal(want) / Decimal(base), Decimal(1))
    items = []
    for src in original_items:
        price = int((Decimal(int(src.get("price") or 0)) * ratio).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        item = dict(src)
        item.update({"price": price, "discount": 0, "other": 0, "vat": vat_of(price, int(src.get("vatPercent") or 0))})
        items.append(item)
    return fit_total(items, min(want, base), {"vat_percent": int(original_items[-1].get("vatPercent") or 0)})


def sale_method(cfg: dict, kind: str) -> str:
    """Chek turi → E-POS usuli."""
    if kind == "refund":
        return "refund"
    if kind == "advance":
        return "advance"
    return "sale" if cfg["epos_prints"] else "fastSale"


def build_request(
    cfg: dict,
    *,
    kind: str,
    number: str,
    items: list[dict],
    cash: float,
    card: float,
    staff_name: str = "",
    client_name: str = "",
    note: str = "",
    refund_info: dict | None = None,
) -> dict:
    """E-POS so'rovi (token'siz — u yuborish paytida qo'shiladi)."""
    params: dict = {
        "paycheckNumber": number,
        # Tashqi kalit: qayta yuborishdan oldin `checkReceiptIfExists` shu
        # raqam bo'yicha chek allaqachon yaratilganini aniqlaydi
        "externalId": number,
        "items": items,
        "receivedCash": to_tiyin(cash),
        "receivedCard": to_tiyin(card),
    }
    if client_name:
        params["clientName"] = _text(client_name, 120)
    if note:
        params["note"] = _text(note, 200)
    request: dict = {
        "method": sale_method(cfg, kind),
        "companyName": cfg["company_name"],
        "companyAddress": cfg["company_address"],
        "companyINN": cfg["company_inn"],
        "staffName": _text(staff_name, 80) or "Kassir",
        "printerSize": cfg["printer_size"],
        "params": params,
    }
    if cfg["company_phone"]:
        request["companyPhoneNumber"] = cfg["company_phone"]
    if kind == "refund" and refund_info:
        request["refundInfo"] = {
            "terminalId": str(refund_info.get("terminal_id") or ""),
            "receiptSeq": str(refund_info.get("receipt_seq") or ""),
            "dateTime": str(refund_info.get("fiscal_datetime") or ""),
            "fiscalSign": str(refund_info.get("fiscal_sign") or ""),
        }
    return request


def parse_result(body: dict | None) -> dict:
    """E-POS javobi → `{ok, error, terminal_id, receipt_seq, fiscal_sign,
    fiscal_datetime, qr_url}`. `sale` javobida ma'lumot `info` da,
    `fastSale` da `message` da, tekshiruvda `data.receipt` da keladi."""
    if not isinstance(body, dict):
        return {"ok": False, "error": "E-POS javobi tushunarsiz"}
    if body.get("error"):
        message = body.get("message")
        return {"ok": False, "error": " ".join(str(message or "E-POS xatosi").split())}
    info = None
    for key in ("info", "message"):
        if isinstance(body.get(key), dict):
            info = body[key]
            break
    if info is None and isinstance(body.get("data"), dict):
        info = body["data"].get("receipt") if isinstance(body["data"].get("receipt"), dict) else None
    info = info or {}
    get = lambda *names: next((str(info[n]) for n in names if info.get(n) not in (None, "")), "")  # noqa: E731
    out = {
        "ok": True,
        "error": None,
        "terminal_id": get("terminalId", "terminal_id"),
        "receipt_seq": get("receiptSeq", "receipt_seq"),
        "fiscal_sign": get("fiscalSign", "fiscal_sign"),
        "fiscal_datetime": get("dateTime", "date_time"),
        "qr_url": get("qrCodeURL", "qr_code_url"),
    }
    if not out["fiscal_sign"]:
        return {"ok": False, "error": "E-POS javobida fiskal belgi yo'q"}
    return out


#: Javoblarda HECH QACHON chiqmaydigan maydonlar (token — ochiq ham, shifrlangan ham)
SECRET_FIELDS = ("token_enc", "token_plain")
#: Bazaga yozilmaydigan (hisoblab chiqariladigan) maydonlar
DERIVED_FIELDS = ("token_plain", "token_set")


def public(cfg: dict) -> dict:
    """Sozlamaning javobda ko'rsatiladigan qismi — tokensiz."""
    return {k: v for k, v in cfg.items() if k not in SECRET_FIELDS}


def enabled_since(cfg: dict) -> datetime | None:
    raw = cfg.get("enabled_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None
