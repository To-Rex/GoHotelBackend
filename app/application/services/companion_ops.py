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
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable
from uuid import UUID

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
