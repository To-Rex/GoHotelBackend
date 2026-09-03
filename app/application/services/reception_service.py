"""Qabulxona uchun mobil ma'lumotlar.

Resepsiya xodimi telefonda ish ko'radi: qo'lida katta ekran yo'q, unga
kerak bo'ladigan javob esa qisqa — "bugun kim keladi, kim turibdi, kim
chiqadi". Shuning uchun bu yerda bitta boyitilgan ro'yxat tayyorlanadi:
mehmon nomi, xona raqami va to'lov holati bilan.

Mobil ilova bunday ro'yxatni o'zi yig'a olmaydi: `/reservations` faqat
ID'larni beradi va mehmon bilan xonani alohida so'rash kerak bo'lardi —
telefon tarmog'ida bu uch marta kutish degani.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room

#: Ro'yxatda ko'rinadigan bron turlari — bekor qilinganlari standart
#: holda chiqmaydi, lekin so'ralsa ko'rsatiladi.
DEFAULT_HIDDEN_STATUSES = ("CANCELLED",)

#: Bron tanlangan kunga qanday tegishli ekani. Resepsiya kunni shu uch
#: guruh bilan o'ylaydi.
KIND_ARRIVAL = "arrival"
KIND_INHOUSE = "inhouse"
KIND_DEPARTURE = "departure"


def _digits(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


#: Telefon raqamidagi ajratgichlar. Bazada raqam har xil ko'rinishda
#: saqlanadi: "+998 90 123 45 67", "+998901234567", "(90) 123-45-67".
PHONE_SEPARATORS = (" ", "-", "+", "(", ")", ".")


def _phone_digits(column):
    """Ustundagi raqamdan ajratgichlarni olib tashlaydi.

    Qidiruvda raqam formatlash belgilari to'sqinlik qilmasligi kerak:
    xodim "901234567" deb yozganda bazadagi "+998 90 123 45 67" ham
    topilishi kerak edi.

    `regexp_replace` o'rniga oddiy `replace` zanjiri ishlatiladi — u
    barcha bazalarda bir xil ishlaydi va bu yerda aniq nechta belgi
    olib tashlanayotgani ko'rinib turadi.
    """
    expression = func.coalesce(column, "")
    for separator in PHONE_SEPARATORS:
        expression = func.replace(expression, separator, "")
    return expression


def kind_for(reservation: Reservation, day: date) -> str:
    """Bron shu kunda kelishmi, turishmi yoki chiqishmi.

    Bir kunlik bron ham kelish, ham chiqish bo'ladi — bunday holatda
    KELISH deb belgilanadi: resepsiya uchun avval mehmonni joylashtirish
    muhim, chiqish keyin bo'ladi.
    """
    if reservation.check_in_date == day:
        return KIND_ARRIVAL
    if reservation.check_out_date == day:
        return KIND_DEPARTURE
    return KIND_INHOUSE


class ReceptionService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def bookings(
        self,
        hotel_id: UUID,
        day: date,
        *,
        status: str | None = None,
        search: str | None = None,
        include_cancelled: bool = False,
        limit: int = 200,
    ) -> list[dict]:
        """Tanlangan kunga tegishli bronlar — mehmon va xona bilan.

        Kun bo'yicha shart `check_in_date <= day <= check_out_date`:
        kelayotganlar, turganlar va chiqayotganlar bitta ro'yxatda
        bo'ladi. Resepsiya kunni aynan shunday ko'radi.
        """
        stmt = (
            select(Reservation, Guest, Room)
            .join(Guest, Guest.id == Reservation.guest_id)
            .join(Room, Room.id == Reservation.room_id)
            .where(
                Reservation.hotel_id == hotel_id,
                Reservation.is_deleted.is_(False),
                Reservation.check_in_date <= day,
                Reservation.check_out_date >= day,
            )
        )

        if status:
            stmt = stmt.where(Reservation.status == status.upper())
        elif not include_cancelled:
            stmt = stmt.where(Reservation.status.notin_(DEFAULT_HIDDEN_STATUSES))

        text = (search or "").strip()
        if text:
            like = f"%{text}%"
            conditions = [
                Reservation.reservation_number.ilike(like),
                Guest.first_name.ilike(like),
                Guest.last_name.ilike(like),
                Room.room_number.ilike(like),
            ]
            # Raqam bo'yicha qidiruvda formatlash belgilari to'sqinlik
            # qilmasin: bazada "+998 90 123 45 67" turgan bo'lishi mumkin
            digits = _digits(text)
            if len(digits) >= 4:
                # Ustun ham, so'ralgan matn ham faqat raqamga keltiriladi
                conditions.append(_phone_digits(Guest.phone).like(f"%{digits}%"))
            else:
                conditions.append(Guest.phone.ilike(like))
            stmt = stmt.where(or_(*conditions))

        # Kelayotganlar ro'yxat boshida turishi uchun xona raqami emas,
        # kirish sanasi bo'yicha saralanadi
        stmt = stmt.order_by(
            Reservation.check_in_date.desc(), Room.room_number
        ).limit(limit)

        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "id": str(reservation.id),
                "reservation_number": reservation.reservation_number,
                "guest_id": str(guest.id),
                "guest_name": f"{guest.first_name or ''} {guest.last_name or ''}".strip()
                or None,
                "guest_phone": guest.phone,
                "room_id": str(room.id),
                "room_number": room.room_number,
                "booking_type": reservation.booking_type,
                "check_in_date": reservation.check_in_date.isoformat(),
                "check_out_date": reservation.check_out_date.isoformat(),
                "check_in_datetime": (
                    reservation.check_in_datetime.isoformat()
                    if reservation.check_in_datetime
                    else None
                ),
                "check_out_datetime": (
                    reservation.check_out_datetime.isoformat()
                    if reservation.check_out_datetime
                    else None
                ),
                "status": reservation.status,
                "payment_status": reservation.payment_status,
                "total_amount": float(reservation.total_amount or 0),
                "paid_amount": float(reservation.paid_amount or 0),
                "adults": reservation.adults,
                "children": reservation.children,
                "notes": reservation.notes,
                "kind": kind_for(reservation, day),
            }
            for reservation, guest, room in rows
        ]
