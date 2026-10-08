"""Kunlik bron hisobi: 12 soatlik yoki 24 soatlik kun.

Mehmonxona sozlamasi (`branches.settings["booking"]["daily_unit"]`):

* ``12h`` (standart, avvalgi tartib) — xona turi narxi BIR kunlik narx:
  kalendarda tanlangan oxirgi kun chiqish kuni, 1 kecha = narx × 1.
* ``24h`` — xona turi narxi 12 soatlik narx deb olinadi va to'liq (24
  soatlik) kun uchun IKKI barobar olinadi: 250 000 so'mlik xona — kuniga
  500 000 so'm. Kalendarda har tanlangan kun to'lanadigan kun bo'ladi.

Rejim bron YARATILGANDA uning o'ziga yoziladi (`reservations.daily_unit`) va
keyingi barcha qayta hisoblar (ko'chirish, chiqish, hisob-faktura) shu
qiymatdan foydalanadi. Ya'ni sozlama keyin o'zgartirilsa ham mavjud bronlar
narxi o'zgarmaydi; sozlamadan oldin yaratilgan bronlar — ``12h``.

Soatlik bronlarga tegishli emas (koeffitsient doim 1).
"""
from __future__ import annotations

DAILY_UNITS = ("12h", "24h")
DEFAULT_DAILY_UNIT = "12h"
BOOKING_SETTINGS_KEY = "booking"

#: 24 soatlik kun narxi — 12 soatlik narxning necha barobari
FULL_DAY_MULTIPLIER = 2


def normalize_daily_unit(value) -> str:
    """Notanish yoki bo'sh qiymat — standart (12 soatlik)."""
    return value if value in DAILY_UNITS else DEFAULT_DAILY_UNIT


def resolve_daily_unit(hotel_settings: dict | None) -> str:
    """Mehmonxona sozlamasidagi kunlik hisob rejimi."""
    saved = (hotel_settings or {}).get(BOOKING_SETTINGS_KEY)
    if not isinstance(saved, dict):
        return DEFAULT_DAILY_UNIT
    return normalize_daily_unit(saved.get("daily_unit"))


def daily_multiplier(booking_type: str | None, daily_unit: str | None) -> int:
    """Kunlik narx koeffitsienti: 24 soatlik kunlik bronda 2, aks holda 1."""
    if (booking_type or "DAILY") == "HOURLY":
        return 1
    return FULL_DAY_MULTIPLIER if normalize_daily_unit(daily_unit) == "24h" else 1


def unit_price(base_price: float, booking_type: str | None, daily_unit: str | None) -> float:
    """Bir kecha/kun (yoki soatlik bron) uchun olinadigan narx."""
    return float(base_price or 0) * daily_multiplier(booking_type, daily_unit)


def reservation_daily_unit(reservation) -> str:
    """Bronga yozilgan rejim; eski (ustunsiz) yozuv — 12 soatlik."""
    return normalize_daily_unit(getattr(reservation, "daily_unit", None))
