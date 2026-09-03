"""Kiruvchi qo'ng'iroq bo'yicha mehmonni topish.

Mehmon qo'ng'iroq qilganda resepsiya xodimi "kim gapiryapti" degan
savolga javob izlaydi: ismini so'raydi, keyin qidiradi, keyin bronini
ochadi. Qurilma raqamni o'zi yuborsa bu ish tugaydi — javob xodim
go'shakni ko'targanda ekranda turadi.

Nozik joy — RAQAM FORMATI. Bazada bir xil raqam "+998 90 123 45 67",
"998901234567" yoki "901234567" bo'lib yotgan bo'lishi mumkin. Shuning
uchun ikkala tomon ham faqat raqamga keltiriladi va oxirgi
`MATCH_DIGITS` ta belgi bo'yicha solishtiriladi: mamlakat kodi bor-yo'qligi
natijaga ta'sir qilmaydi.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ValidationException
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.incoming_call import IncomingCall
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room

#: Solishtirish uchun raqamning oxirgi nechta belgisi olinadi.
#: O'zbekistonda milliy raqam 9 xonali (90 123 45 67) — mamlakat kodi
#: bor-yo'qligidan qat'i nazar shu qism bir xil bo'ladi.
MATCH_DIGITS = 9

#: Bundan qisqa raqam ichki nomer bo'lishi mumkin — u to'liq
#: solishtiriladi, aks holda tasodifiy mos kelish chiqardi.
MIN_MATCH_DIGITS = 5

#: Menyuda qo'ng'iroq shuncha vaqt ko'rinadi. Undan eskisi javobsiz
#: qolgan bo'lsa ham ekranni band qilib turmasligi kerak.
DEFAULT_WINDOW_MINUTES = 30

#: Bir xil raqamdan shu vaqt ichida kelgan qo'ng'iroq TAKROR hisoblanadi.
#: Android bitta qo'ng'iroq uchun bir necha marta xabar berishi mumkin
#: (RINGING holati qayta-qayta keladi) — ro'yxat dublikatga to'lmasin.
DEDUPE_SECONDS = 90

#: Xona qidirilganda hisobga olinadigan bron holatlari.
ACTIVE_STATUSES = ("CONFIRMED", "CHECKED_IN")

PHONE_SEPARATORS = (" ", "-", "+", "(", ")", ".")


def normalize(phone: str | None) -> str:
    """Raqamdan faqat raqamlarni qoldiradi."""
    return "".join(ch for ch in (phone or "") if ch.isdigit())


def match_key(digits: str) -> str:
    """Solishtirish uchun raqamning oxirgi qismi."""
    return digits[-MATCH_DIGITS:] if len(digits) > MATCH_DIGITS else digits


def _digits_expression(column):
    """Ustundagi raqamdan ajratgichlarni olib tashlaydi."""
    expression = func.coalesce(column, "")
    for separator in PHONE_SEPARATORS:
        expression = func.replace(expression, separator, "")
    return expression


class IncomingCallService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------- qidiruv --

    async def find_guest(self, hotel_id: UUID, digits: str) -> Guest | None:
        """Raqam bo'yicha mehmon.

        Mehmonlar bazasi bu loyihada GLOBAL — `hotel_id` bo'yicha filtr
        qo'llanmaydi (`guest_service` dagi izohga qarang). Mehmonxona
        argumenti kelajakdagi cheklov uchun qoldirilgan.
        """
        key = match_key(digits)
        if len(key) < MIN_MATCH_DIGITS:
            return None

        stmt = select(Guest).where(
            _digits_expression(Guest.phone).like(f"%{key}"),
        )
        # Bir xil raqamli bir nechta yozuv bo'lsa oxirgi kiritilgani
        # olinadi: eskisi ko'pincha to'liqsiz karta bo'ladi
        stmt = stmt.order_by(desc(Guest.created_at)).limit(1)
        return (await self.session.execute(stmt)).scalars().first()

    async def _active_stay(
        self, hotel_id: UUID, guest_id: UUID, day: date
    ) -> tuple[UUID, str] | None:
        """Mehmonning shu kundagi faol broni va xonasi."""
        row = (
            await self.session.execute(
                select(Reservation.id, Room.room_number)
                .join(Room, Room.id == Reservation.room_id)
                .where(
                    Reservation.hotel_id == hotel_id,
                    Reservation.guest_id == guest_id,
                    Reservation.is_deleted.is_(False),
                    Reservation.status.in_(ACTIVE_STATUSES),
                    Reservation.check_in_date <= day,
                    Reservation.check_out_date >= day,
                )
                .order_by(desc(Reservation.check_in_date))
                .limit(1)
            )
        ).first()
        return (row[0], row[1]) if row else None

    # -------------------------------------------------------- yozish --

    async def record(
        self,
        hotel_id: UUID,
        phone: str,
        *,
        reported_by: UUID | None = None,
        device_id: str | None = None,
        today: date | None = None,
    ) -> dict:
        """Qo'ng'iroqni qayd etadi va topilgan mehmonni qaytaradi."""
        digits = normalize(phone)
        if len(digits) < MIN_MATCH_DIGITS:
            raise ValidationException(
                "Telefon raqami juda qisqa yoki noto'g'ri", "INVALID_PHONE"
            )

        now = datetime.now(timezone.utc)

        # TAKROR: bitta qo'ng'iroq uchun Android bir necha marta xabar
        # berishi mumkin — yaqindagi yozuv qaytariladi, yangisi ochilmaydi.
        #
        # Solishtirish TO'LIQ raqam bo'yicha emas, oxirgi qismi bo'yicha:
        # bitta qo'ng'iroq "+998901234567" va "901234567" ko'rinishida
        # kelishi mumkin va ular bir xil raqam hisoblanishi kerak.
        key = match_key(digits)
        recent = (
            await self.session.execute(
                select(IncomingCall)
                .where(
                    IncomingCall.hotel_id == hotel_id,
                    IncomingCall.phone_digits.like(f"%{key}"),
                    IncomingCall.received_at
                    >= now - timedelta(seconds=DEDUPE_SECONDS),
                )
                .order_by(desc(IncomingCall.received_at))
                .limit(1)
            )
        ).scalars().first()
        if recent is not None:
            return self._as_dict(recent, duplicate=True)

        guest = await self.find_guest(hotel_id, digits)
        stay = (
            await self._active_stay(hotel_id, guest.id, today or now.date())
            if guest
            else None
        )

        call = IncomingCall(
            hotel_id=hotel_id,
            phone=phone.strip(),
            phone_digits=digits,
            guest_id=guest.id if guest else None,
            guest_name=(
                f"{guest.first_name or ''} {guest.last_name or ''}".strip() or None
                if guest
                else None
            ),
            reservation_id=stay[0] if stay else None,
            room_number=stay[1] if stay else None,
            device_id=device_id,
            reported_by=reported_by,
        )
        self.session.add(call)
        await self.session.flush()
        # `received_at` server tomonida to'ldiriladi — javobda haqiqiy
        # qiymat turishi uchun qayta o'qiladi
        await self.session.refresh(call)
        return self._as_dict(call)

    # -------------------------------------------------------- o'qish --

    async def recent(
        self,
        hotel_id: UUID,
        *,
        minutes: int = DEFAULT_WINDOW_MINUTES,
        include_acknowledged: bool = False,
        limit: int = 20,
    ) -> list[dict]:
        """Oxirgi qo'ng'iroqlar — veb menyusi uchun."""
        stmt = select(IncomingCall).where(
            IncomingCall.hotel_id == hotel_id,
            IncomingCall.received_at
            >= datetime.now(timezone.utc) - timedelta(minutes=minutes),
        )
        if not include_acknowledged:
            stmt = stmt.where(IncomingCall.acknowledged_at.is_(None))
        stmt = stmt.order_by(desc(IncomingCall.received_at)).limit(limit)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._as_dict(row) for row in rows]

    async def acknowledge(
        self, call_id: UUID, hotel_id: UUID, user_id: UUID
    ) -> dict:
        call = (
            await self.session.execute(
                select(IncomingCall).where(
                    IncomingCall.id == call_id,
                    IncomingCall.hotel_id == hotel_id,
                )
            )
        ).scalar_one_or_none()
        if call is None:
            raise NotFoundException("Call not found", "CALL_NOT_FOUND")
        if call.acknowledged_at is None:
            call.acknowledged_at = datetime.now(timezone.utc)
            call.acknowledged_by = user_id
            await self.session.flush()
        return self._as_dict(call)

    @staticmethod
    def _as_dict(call: IncomingCall, duplicate: bool = False) -> dict:
        return {
            "id": str(call.id),
            "phone": call.phone,
            "guest_id": str(call.guest_id) if call.guest_id else None,
            "guest_name": call.guest_name,
            "reservation_id": (
                str(call.reservation_id) if call.reservation_id else None
            ),
            "room_number": call.room_number,
            "matched": call.guest_id is not None,
            "received_at": (
                call.received_at.isoformat() if call.received_at else None
            ),
            "acknowledged": call.acknowledged_at is not None,
            # Qurilmaga aytiladi: bu yozuv yangi emas, oldingisi qaytdi
            "duplicate": duplicate,
        }
