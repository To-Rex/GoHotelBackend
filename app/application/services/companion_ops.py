"""Bron hamrohlari ustidagi amallar — turish davomida kim keldi, kim ketdi.

Hamrohlar `Reservation.companions` JSONB ro'yxatida saqlanadi:

    {"guest_id": "...", "name": "..."}              — bron yaratilganda
    + "added_at", "added_by"                        — turish davomida qo'shilgan
    + "left_at", "left_by"                          — xonadan ketgan
    + "returned_at", "returned_by"                  — ketib, yana qaytib kelgan

Yozuv ro'yxatdan O'CHIRILMAYDI (faqat kirish rasmiylashtirilmagan bronda
mumkin): ketgan hamroh ham shu xonada turgan — mehmon tarixi, hisobot va
kamera moslashuvi uchun bu muhim. "Ketdi" belgisi faqat hozir kim ichkarida
ekanini bildiradi. Ketgan o'rniga boshqa odam kelsa, bo'sh joy shu belgi
bo'yicha hisoblanadi: ichkaridagilar (asosiy mehmon + ketmagan hamrohlar)
bron qilingan mehmonlar sonidan oshmaydi.

Hamma funksiya sof: kirish ro'yxati o'zgartirilmaydi, YANGI ro'yxat
qaytariladi — JSONB ustuni o'zgarishni faqat yangi obyekt berilganda sezadi.
Eski yozuvlar (qo'shimcha kalitlarsiz) o'z holicha ishlaydi.

KECHIKIB KELADIGAN hamrohlar `Reservation.expected_companions` da alohida:

    {"id": "...", "name": ..., "phone": ..., "note": ...,
     "created_at": "...", "created_by": "..."}

Ular hali mehmon emas (bazada yozuvi yo'q, xonada yo'q) — faqat "keyin yana
bir kishi keladi" degan va'da. Shuning uchun `companions` ga aralashtirilmaydi
(u yerdagi har yozuv haqiqiy mehmon: tarix, hisobot, kamera). Lekin JOY
egallaydi: ichkaridagilar + kutilayotganlar mehmonlar sonidan oshmaydi, va
"har bir mehmon ro'yxatga olinsin" rejimida kutilayotgan kishi "hisobga
olingan" sanaladi. Kelganda `attach_companion` uni haqiqiy hamrohga
aylantiradi (yozuvda `arrived_late`, `expected_since`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable
from uuid import UUID, uuid4

from app.core.exceptions import NotFoundException, ValidationException

#: Ketishga tegishli kalitlar — qaytishda olib tashlanadi
_LEFT_KEYS = ("left_at", "left_by")


def companion_guest_id(entry: dict | None) -> str | None:
    raw = (entry or {}).get("guest_id")
    return str(raw) if raw else None


def is_present(entry: dict | None) -> bool:
    """Hamroh hozir xonadami — "ketdi" belgisi yo'q."""
    return not (entry or {}).get("left_at")


def present_count(companions: Iterable[dict] | None) -> int:
    """Xonadagi hamrohlar soni (asosiy mehmon hisobga olinmaydi)."""
    return sum(1 for c in companions or [] if is_present(c))


def find_companion(companions: Iterable[dict] | None, guest_id) -> dict | None:
    key = str(guest_id)
    for c in companions or []:
        if companion_guest_id(c) == key:
            return c
    return None


def _copy(companions: Iterable[dict] | None) -> list[dict]:
    return [dict(c) for c in companions or [] if c]


def _replace(companions: Iterable[dict] | None, guest_id, updated: dict) -> list[dict]:
    key = str(guest_id)
    return [updated if companion_guest_id(c) == key else dict(c) for c in companions or [] if c]


def _ensure_seat(companions: Iterable[dict] | None, adults: int) -> None:
    """Yana bir kishi sig'adimi: ichkaridagilar (asosiy mehmon + ketmagan
    hamrohlar) + 1 bron qilingan mehmonlar sonidan oshmasin."""
    inside = present_count(companions) + 1  # + asosiy mehmon
    capacity = max(int(adults or 1), 1)
    if inside + 1 > capacity:
        raise ValidationException(
            f"Xonada joy yo'q: mehmonlar soni {capacity}, hozir {inside} kishi "
            "ichkarida. Avval ketgan hamrohni belgilang yoki bronni tahrirlab "
            "mehmonlar sonini oshiring",
            "ROOM_GUESTS_FULL",
        )


def add_companion(
    companions: Iterable[dict] | None,
    *,
    guest_id,
    name: str | None,
    main_guest_id,
    adults: int,
    at: datetime,
    by: UUID,
) -> list[dict]:
    """Turish davomida yangi hamroh — ketgan o'rniga kelgan yoki bo'sh joyga.

    Ichkaridagilar soni bron qilingan mehmonlar sonidan (`adults`) oshmaydi:
    joy bo'lmasa avval ketganini belgilash yoki bronni tahrirlab sonni
    oshirish kerak — bu qoida yaratishdagi bilan bir xil. Ilgari ketgan
    hamroh qaytib kelsa YANGI yozuv ochilmaydi (bir odam ro'yxatda ikki
    marta turmasin) — ketish belgisi tushadi, qaytish vaqti yoziladi.
    """
    if str(guest_id) == str(main_guest_id):
        raise ValidationException(
            "Asosiy mehmon hamroh sifatida qo'shilmaydi", "COMPANION_IS_MAIN_GUEST"
        )
    existing = find_companion(companions, guest_id)
    if existing is not None and is_present(existing):
        raise ValidationException("Bu mehmon allaqachon xonada", "COMPANION_ALREADY_PRESENT")

    _ensure_seat(companions, adults)

    stamp = at.isoformat()
    if existing is not None:
        returned = {k: v for k, v in existing.items() if k not in _LEFT_KEYS}
        returned.update({"returned_at": stamp, "returned_by": str(by)})
        return _replace(companions, guest_id, returned)

    entry = {
        "guest_id": str(guest_id),
        "name": name or None,
        "added_at": stamp,
        "added_by": str(by),
    }
    return _copy(companions) + [entry]


def mark_left(companions: Iterable[dict] | None, guest_id, *, at: datetime, by: UUID) -> list[dict]:
    """Hamroh xonadan ketdi — yozuv qoladi, "ketdi" belgisi qo'yiladi."""
    existing = find_companion(companions, guest_id)
    if existing is None:
        raise NotFoundException("Hamroh topilmadi", "COMPANION_NOT_FOUND")
    if not is_present(existing):
        raise ValidationException(
            "Bu hamroh allaqachon ketgan deb belgilangan", "COMPANION_ALREADY_LEFT"
        )
    updated = {**existing, "left_at": at.isoformat(), "left_by": str(by)}
    return _replace(companions, guest_id, updated)


def mark_returned(companions: Iterable[dict] | None, guest_id, *, adults: int) -> list[dict]:
    """"Ketdi" belgisini bekor qilish (adashib bosilgan bo'lsa).

    Joy tekshiriladi: ketgan o'rniga boshqasi kirgan bo'lsa, bekor qilish
    xonani sig'imidan oshirib yuborardi."""
    existing = find_companion(companions, guest_id)
    if existing is None:
        raise NotFoundException("Hamroh topilmadi", "COMPANION_NOT_FOUND")
    if is_present(existing):
        raise ValidationException(
            "Bu hamroh ketgan deb belgilanmagan", "COMPANION_NOT_LEFT"
        )
    _ensure_seat(companions, adults)
    updated = {k: v for k, v in existing.items() if k not in _LEFT_KEYS}
    return _replace(companions, guest_id, updated)


def remove_companion(companions: Iterable[dict] | None, guest_id) -> list[dict]:
    """Ro'yxatdan butunlay olib tashlash — faqat kirishdan OLDIN (adashib
    qo'shilgan). Kirgan bronda `mark_left` ishlatiladi: tarix saqlanadi."""
    if find_companion(companions, guest_id) is None:
        raise NotFoundException("Hamroh topilmadi", "COMPANION_NOT_FOUND")
    key = str(guest_id)
    return [dict(c) for c in companions or [] if c and companion_guest_id(c) != key]


# --------------------------------------------- kechikib keladigan hamrohlar --

#: Bitta bronda kutilayotgan hamrohlar chegarasi (yaratishdagi bilan bir xil)
MAX_EXPECTED = 20


def _clean(value, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] or None


def expected_count(expected: Iterable[dict] | None) -> int:
    return sum(1 for e in expected or [] if e)


def find_expected(expected: Iterable[dict] | None, expected_id) -> dict | None:
    key = str(expected_id)
    for e in expected or []:
        if e and str(e.get("id")) == key:
            return e
    return None


def new_expected_entry(*, name=None, phone=None, note=None, at: datetime, by: UUID | None) -> dict:
    """Bitta "keyin keladi" yozuvi — ismi/telefoni ixtiyoriy (qabulxona
    kimni kutayotganini bilsin)."""
    return {
        "id": uuid4().hex,
        "name": _clean(name, 120),
        "phone": _clean(phone, 32),
        "note": _clean(note, 200),
        "created_at": at.isoformat(),
        "created_by": str(by) if by else None,
    }


def _ensure_room_for_expected(companions, expected, adults: int) -> None:
    inside = present_count(companions) + 1
    capacity = max(int(adults or 1), 1)
    waiting = expected_count(expected)
    if inside + waiting + 1 > capacity:
        raise ValidationException(
            f"Xonada joy yo'q: mehmonlar soni {capacity}, ichkarida {inside} kishi"
            + (f", yana {waiting} kishi kutilmoqda" if waiting else "")
            + ". Bronni tahrirlab mehmonlar sonini oshiring",
            "ROOM_GUESTS_FULL",
        )


def add_expected(
    companions: Iterable[dict] | None,
    expected: Iterable[dict] | None,
    *,
    adults: int,
    name=None,
    phone=None,
    note=None,
    at: datetime,
    by: UUID | None,
) -> list[dict]:
    """"Yana bir hamroh kechikib keladi" — joy band qilinadi."""
    if expected_count(expected) >= MAX_EXPECTED:
        raise ValidationException("Kutilayotgan hamrohlar juda ko'p", "TOO_MANY_EXPECTED")
    _ensure_room_for_expected(companions, expected, adults)
    entry = new_expected_entry(name=name, phone=phone, note=note, at=at, by=by)
    return [dict(e) for e in expected or [] if e] + [entry]


def remove_expected(expected: Iterable[dict] | None, expected_id) -> list[dict]:
    """Kutilgan hamroh kelmadi / adashib belgilangan — joy bo'shaydi."""
    if find_expected(expected, expected_id) is None:
        raise NotFoundException("Kutilayotgan hamroh topilmadi", "EXPECTED_NOT_FOUND")
    key = str(expected_id)
    return [dict(e) for e in expected or [] if e and str(e.get("id")) != key]


def attach_companion(
    companions: Iterable[dict] | None,
    expected: Iterable[dict] | None,
    *,
    guest_id,
    name: str | None,
    main_guest_id,
    adults: int,
    at: datetime,
    by: UUID,
    expected_id=None,
) -> tuple[list[dict], list[dict], dict | None]:
    """Hamroh keldi — xonaga qo'shiladi. Qaytaradi: (hamrohlar, kutilayotganlar,
    o'rniga kelgan kutilgan yozuv yoki None).

    * `expected_id` berilsa — aynan o'sha kutilgan hamroh keldi.
    * Berilmasa va joy FAQAT kutilayotganlar hisobiga band bo'lsa — kelgan
      odam kutilgan hamroh deb olinadi (eng birinchisi): xodim umumiy
      "Hamroh qo'shish" tugmasini bossa ham joy topiladi.
    * Bo'sh joy bo'lsa — oddiy yangi hamroh, kutilayotganlar o'zgarmaydi.
    """
    waiting = [dict(e) for e in expected or [] if e]
    consumed: dict | None = None
    if expected_id is not None:
        consumed = find_expected(waiting, expected_id)
        if consumed is None:
            raise NotFoundException("Kutilayotgan hamroh topilmadi", "EXPECTED_NOT_FOUND")
        waiting = [e for e in waiting if str(e.get("id")) != str(expected_id)]
    elif waiting:
        capacity = max(int(adults or 1), 1)
        already = find_companion(companions, guest_id)
        joining = 0 if (already is not None and is_present(already)) else 1
        if present_count(companions) + 1 + len(waiting) + joining > capacity:
            consumed, waiting = waiting[0], waiting[1:]

    updated = add_companion(
        companions,
        guest_id=guest_id,
        name=name,
        main_guest_id=main_guest_id,
        adults=adults,
        at=at,
        by=by,
    )
    # Qolgan kutilayotganlar uchun ham joy bo'lishi kerak
    capacity = max(int(adults or 1), 1)
    if present_count(updated) + 1 + len(waiting) > capacity:
        raise ValidationException(
            f"Xonada joy yo'q: mehmonlar soni {capacity}, yana {len(waiting)} kishi "
            "kechikib kelishi kutilmoqda. Kelgan odam o'sha kutilgan hamroh bo'lsa "
            "\"Keldi\" tugmasidan biriktiring, kelmasa uni bekor qiling",
            "ROOM_GUESTS_FULL",
        )

    if consumed is not None:
        key = str(guest_id)
        marked = []
        for c in updated:
            if companion_guest_id(c) == key:
                c = {
                    **c,
                    "arrived_late": True,
                    "expected_since": consumed.get("created_at"),
                    "expected_name": consumed.get("name"),
                }
            marked.append(c)
        updated = marked
    return updated, waiting, consumed
