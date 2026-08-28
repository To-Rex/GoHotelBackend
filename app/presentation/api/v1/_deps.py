from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.shift import ShiftSession
from app.presentation.middleware.auth import get_current_user


async def require_active_hotel(
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    if current_user.get("user_type") == "SUPER_ADMIN":
        return
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    hotel = await session.get(Hotel, hotel_id)
    if not hotel:
        raise ForbiddenException("Hotel not found")
    if hotel.status != "ACTIVE":
        raise ForbiddenException(
            f"Hotel is {hotel.status}. Operations are blocked for non-active hotels."
        )


# Kassa bilan ishlaydigan xodim: bron yoki to'lov yaratadigan (qabulxona,
# kassir). Farrosh va texnik xodim smena tizimiga tortilmaydi — ularning ishi
# kassaga bog'liq emas. Menejer (shift.force_close egasi) ham mustasno: u
# smena ochmasdan tuzatish kiritishi kerak bo'ladi.
CASH_PERMISSIONS = ("finance.payment.create", "reservation.create")


def _is_cash_staff(current_user: dict) -> bool:
    if current_user.get("user_type") != "EMPLOYEE":
        return False
    permissions = current_user.get("permissions") or []
    if "shift.force_close" in permissions:
        return False
    return any(code in permissions for code in CASH_PERMISSIONS)


async def require_open_shift(
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Kassali rejimda bron/to'lov faqat OCHIQ smenada yaratilishi mumkin.

    Kassa hisobi xodimning sessiyasiga bog'langan: tushum sessiya oynasiga
    tushmasa, u hech kimning kassasiga yozilmaydi va smena topshirishda pul
    "yo'q joydan" paydo bo'ladi. Frontend buni yashiradi, lekin bu yerdagi
    tekshiruvsiz to'g'ridan-to'g'ri so'rov baribir o'tib ketardi.

    Cheklov FAQAT kassali rejimdagi kassa xodimlariga tegishli — administrator,
    menejer va kassaga aloqasi yo'q xodimlar avvalgidek ishlayveradi.
    """
    if not _is_cash_staff(current_user):
        return
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        return

    from app.application.services.shift_service import resolve_shift_settings

    hotel = await session.get(Hotel, hotel_id)
    if resolve_shift_settings(hotel.settings if hotel else None)["mode"] != "cash":
        return

    open_session = (
        await session.execute(
            select(ShiftSession.id)
            .where(
                ShiftSession.hotel_id == hotel_id,
                ShiftSession.user_id == current_user["id"],
                ShiftSession.status == "ACTIVE",
            )
            .limit(1)
        )
    ).scalar()
    if not open_session:
        raise ForbiddenException(
            "Smena ochilmagan — avval «Mening hisobotim» sahifasida smenani boshlang",
            "SHIFT_NOT_OPEN",
        )
