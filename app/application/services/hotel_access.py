"""Mehmonxona to'xtatilganda kirishni to'sish.

Panel mehmonxonani o'chirmaydi, faqat holatini `INACTIVE` (yoki
`SUSPENDED`) ga o'tkazadi. Shundan keyin xodim tizimga kirsa, so'rovlari
403 bilan qaytardi — lekin javobda sabab MASHINA O'QIY OLADIGAN shaklda
yo'q edi, shuning uchun klient uni oddiy xatodan ajrata olmasdi va ekran
cheksiz "yuklanmoqda" holatida qolib ketardi.

Shu sabab bu yerda ikki narsa aniq belgilanadi:

* `error_code` — `HOTEL_INACTIVE` / `HOTEL_SUSPENDED`. Klient aynan shu
  kodga qarab "xizmat to'xtatilgan" ekranini ko'rsatadi.
* `detail` — foydalanuvchi o'qiydigan matn. Ilgari inglizcha texnik
  jumla edi.

Tekshiruv ikki joyda ishlaydi: kirishda (`auth_service`) va har bir
so'rovda (`_deps.require_active_hotel`). Faqat kirishda tekshirish
yetarli emas — allaqachon kirgan xodim token muddatigacha ishlab
yuraverardi.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenException
from app.infrastructure.database.models.hotel import Hotel

#: Ishlashga ruxsat beradigan yagona holat.
ACTIVE_STATUS = "ACTIVE"

_MESSAGES = {
    "INACTIVE": (
        "Mehmonxona xizmati to'xtatilgan. Barcha ma'lumot va tarix "
        "saqlanmoqda — xizmatni tiklash uchun tizim ma'muriga murojaat "
        "qiling."
    ),
    "SUSPENDED": (
        "Mehmonxona xizmati vaqtincha to'xtatilgan. Tizim ma'muriga "
        "murojaat qiling."
    ),
}

_DEFAULT_MESSAGE = (
    "Mehmonxona xizmati to'xtatilgan. Tizim ma'muriga murojaat qiling."
)


def hotel_block_error(hotel: Hotel | None) -> ForbiddenException | None:
    """Mehmonxona ishlay oladimi; ishlay olmasa — tayyor xato.

    `None` qaytsa hammasi joyida. Xato QAYTARILADI (ko'tarilmaydi):
    chaqiruvchi uni o'z kontekstida ko'taradi va shu bilan tekshiruvni
    testda qulay ishlatish mumkin.
    """
    if hotel is None:
        return ForbiddenException("Mehmonxona topilmadi", "HOTEL_NOT_FOUND")
    status = (hotel.status or "").upper()
    if status == ACTIVE_STATUS:
        return None
    name = (hotel.name or "").strip()
    message = _MESSAGES.get(status, _DEFAULT_MESSAGE)
    if name:
        message = f"{name}: {message}"
    return ForbiddenException(message, f"HOTEL_{status or 'BLOCKED'}")


async def assert_hotel_active(
    session: AsyncSession, hotel_id: UUID | None, user_type: str | None = None
) -> None:
    """Mehmonxona faol bo'lmasa 403 ko'taradi.

    `SUPER_ADMIN` mustasno: uning mehmonxonasi yo'q va u to'xtatilgan
    obyektni tiklash uchun ham kira olishi kerak.
    """
    if user_type == "SUPER_ADMIN":
        return
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    error = hotel_block_error(await session.get(Hotel, hotel_id))
    if error is not None:
        raise error
