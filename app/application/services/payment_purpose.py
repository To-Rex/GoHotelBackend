"""To'lov NIMA UCHUN olingani — Moliya → To'lovlar ro'yxatidagi har qator.

Bron to'lovi hisob-fakturaga UMUMIY tushadi (xona haqi + jarimalar +
xizmatlar), bazada "qaysi haq uchun" yozilmaydi. Shu sababli taqsimot shu
yerda, vaqt tartibida hisoblanadi: har to'lov o'sha paytda mavjud bo'lgan
ENG ESKI to'lanmagan haqni yopadi (FIFO) — xona haqi avval (u birinchi
yozilgan), jarima keyin. Masalan:

    xona 300 000 (chegirma 50 000) → 250 000 to'landi       = Xona
    jarima 50 000 yozildi          → 50 000 to'landi        = Jarima
    xona 200 000 → 100 000 to'landi; jarima 100 000;
    keyin 200 000 to'landi                                   = Xona + Jarima

Chegirma xona haqidan ayiriladi. Haqlardan ortiqcha to'langan qism
"ORTIQCHA" (keyin qaytariladi), manfiy summa — "QAYTARIM". Natija
deterministik va bazaga hech narsa yozilmaydi; hisob-faktura, kassa va
hisobotlar o'zgarmaydi.

"Qarz to'lovi" — mehmon chiqib ketgan BRON uchun chiqish kunidan KEYIN
olingan to'lov (`is_after_checkout`): u ham xona/jarima haqini yopadi,
lekin qarz sifatida alohida belgilanadi.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from app.shared.utils import local_date_of

KIND_ROOM = "ROOM"
KIND_PENALTY = "PENALTY"
KIND_SERVICE = "SERVICE"
KIND_OTHER = "OTHER"
#: Haqlardan ortiqcha to'langan qism (oldindan / keyin qaytariladi)
KIND_EXTRA = "EXTRA"
KIND_REFUND = "REFUND"
KIND_SHOP = "SHOP"
#: Bir nechta haqni birga yopgan to'lov (masalan xona + jarima)
KIND_MIXED = "MIXED"

SOURCE_RESERVATION = "RESERVATION"
SOURCE_SHOP = "SHOP"

LINE_KINDS = {
    "ROOM_CHARGE": KIND_ROOM,
    "PENALTY": KIND_PENALTY,
    "SERVICE": KIND_SERVICE,
}

#: So'mning kasri — yaxlitlash farqi, haq to'langan deb hisoblanadi
EPS = 0.005


@dataclass(frozen=True)
class Charge:
    """Hisob-faktura qatori: nima uchun (kind), qancha, qachon yozilgan."""

    kind: str
    amount: float
    at: datetime | None = None
    label: str | None = None


@dataclass(frozen=True)
class Paid:
    """To'lov: id, summa (manfiy — qaytarim), qachon olingan."""

    id: object
    amount: float
    at: datetime | None = None
    #: Bir xil vaqtli to'lovlar orasidagi o'rin: 0 — oddiy/asl to'lov,
    #: 1 — undan qisman tahrirda ajralgan qism (asl to'lovdan keyin turadi)
    rank: int = 0


def line_kind(line_type: str | None) -> str:
    """Hisob-faktura qatori turi → haq turi (noma'lum — OTHER)."""
    return LINE_KINDS.get((line_type or "").strip().upper(), KIND_OTHER)


def _aware(moment: datetime) -> datetime:
    # Zonasiz qiymat UTC deb olinadi (SQLite testlar shunday qaytaradi)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _order(at: datetime | None, tie: int) -> tuple:
    # Vaqti noma'lum yozuv eng boshiga; bir xil vaqtda haq to'lovdan oldin
    # (ular bitta tranzaksiyada ketma-ket yoziladi)
    if at is None:
        return (datetime.min.replace(tzinfo=timezone.utc), tie)
    return (_aware(at), tie)


def _merge(parts: list[dict], kind: str, amount: float, label: str | None) -> None:
    for part in parts:
        if part["kind"] == kind and part["label"] == label:
            part["amount"] = round(part["amount"] + amount, 2)
            return
    parts.append({"kind": kind, "amount": round(amount, 2), "label": label})


def allocate(
    charges: list[Charge], payments: list[Paid], discount: float = 0.0
) -> dict[object, list[dict]]:
    """Har to'lovni haqlarga taqsimlaydi: `{to'lov id: [{kind, amount, label}]}`.

    Haqlar va to'lovlar vaqt bo'yicha birga yuriladi; to'lov faqat o'sha
    paytgacha yozilgan haqlarni yopadi (keyin yozilgan jarimaga oldingi
    to'lov "tegmaydi"). Qolgani — ORTIQCHA. Manfiy to'lov — QAYTARIM.
    Chegirma (`discount`) birinchi xona haqidan ayiriladi.
    """
    # Kalit: (vaqt, haq/to'lov, rank). Qolgan tenglikda kiritilgan tartib
    # saqlanadi (sort barqaror) — chaqiruvchi ro'yxatni aniq tartibda beradi
    events = [((*_order(c.at, 0), 0), 0, c) for c in charges] + [
        ((*_order(p.at, 1), p.rank), 1, p) for p in payments
    ]
    events.sort(key=lambda e: e[0])

    dues: list[list] = []  # [haq, qolgan]
    discount_left = max(float(discount or 0.0), 0.0)
    result: dict[object, list[dict]] = {}
    for _, is_payment, obj in events:
        if not is_payment:
            due = max(float(obj.amount or 0.0), 0.0)
            if discount_left > EPS and obj.kind == KIND_ROOM:
                taken = min(due, discount_left)
                due -= taken
                discount_left -= taken
            dues.append([obj, due])
            continue

        amount = float(obj.amount or 0.0)
        if amount < 0:
            result[obj.id] = [{"kind": KIND_REFUND, "amount": round(-amount, 2), "label": None}]
            continue

        parts: list[dict] = []
        left = amount
        for entry in dues:
            if left <= EPS:
                break
            charge, due = entry
            if due <= EPS:
                continue
            taken = min(due, left)
            entry[1] = due - taken
            left -= taken
            _merge(parts, charge.kind, taken, charge.label)
        if left > EPS:
            _merge(parts, KIND_EXTRA, left, None)
        result[obj.id] = parts
    return result


def summary_kind(parts: list[dict] | None, amount: float, source: str = SOURCE_RESERVATION) -> str | None:
    """Qator uchun bitta asosiy tur: QAYTARIM / DO'KON / yagona haq / ARALASH."""
    if amount < 0:
        return KIND_REFUND
    if source == SOURCE_SHOP:
        return KIND_SHOP
    kinds: list[str] = []
    for part in parts or []:
        if part["kind"] not in kinds:
            kinds.append(part["kind"])
    if not kinds:
        return None
    return kinds[0] if len(kinds) == 1 else KIND_MIXED


def is_after_checkout(
    created_at: datetime | None,
    status: str | None,
    check_out_date: date | None,
    check_out_datetime: datetime | None = None,
) -> bool:
    """Qarz to'lovi: mehmon CHIQIB KETGAN bronga chiqish kunidan KEYINGI
    kunlarda olingan to'lov.

    Haqiqiy chiqish daqiqasi bazada saqlanmaydi, shuning uchun o'lchov
    KUN: chiqish kunining o'zida (kech chiqish jarimasi bilan) olingan
    to'lov oddiy chiqish to'lovi hisoblanadi — qarz sifatida faqat keyingi
    kunlardagi to'lov belgilanadi. Soatlik bronda chiqish kuni
    `check_out_datetime` ning mahalliy sanasi.
    """
    if created_at is None or (status or "").upper() != "CHECKED_OUT":
        return False
    end = local_date_of(check_out_datetime) if check_out_datetime else check_out_date
    if end is None:
        return False
    return local_date_of(created_at) > end
