"""Tizim bo'ylab ko'rish: bronlar, pul va harakatlar tarixi.

Panelning ikkinchi vazifasi — nazorat. Mehmonxona ichidagi ekranlar
faqat o'z ma'lumotini ko'rsatadi; bu yerda esa hammasi bir joyda:
qaysi obyektda qancha bron bor, qancha pul kelgan, kim nima
o'zgartirgan.

So'rovlar `hotel_id` bo'yicha CHEKLANMAYDI — shu sabab modul alohida
turadi (`estate_service` dagi izohga qarang).
"""
from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.audit_log import AuditLog
from app.infrastructure.database.models.expense import Expense
from app.infrastructure.database.models.floor import Floor
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.payment import Payment
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.room_type import RoomType
from app.infrastructure.database.models.user import User

#: Bitta so'rovda qaytadigan eng ko'p qator.
MAX_ROWS = 200


class InsightService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------- bronlar --

    async def reservations(
        self,
        *,
        hotel_id: UUID | None = None,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> dict:
        """Barcha mehmonxonalardagi bronlar — filtr va sahifalash bilan."""
        conditions = [Reservation.is_deleted.is_(False)]
        if hotel_id:
            conditions.append(Reservation.hotel_id == hotel_id)
        if status:
            conditions.append(Reservation.status == status.upper())
        if date_from:
            conditions.append(Reservation.check_out_date >= date_from)
        if date_to:
            conditions.append(Reservation.check_in_date <= date_to)
        text = (search or "").strip()
        if text:
            like = f"%{text}%"
            conditions.append(
                or_(
                    Reservation.reservation_number.ilike(like),
                    Guest.first_name.ilike(like),
                    Guest.last_name.ilike(like),
                    Room.room_number.ilike(like),
                )
            )

        base = (
            select(Reservation, Guest, Room, Hotel)
            .join(Guest, Guest.id == Reservation.guest_id)
            .join(Room, Room.id == Reservation.room_id)
            .join(Hotel, Hotel.id == Reservation.hotel_id)
            .where(*conditions)
        )

        total = (
            await self.session.execute(
                select(func.count(Reservation.id))
                .select_from(Reservation)
                .join(Guest, Guest.id == Reservation.guest_id)
                .join(Room, Room.id == Reservation.room_id)
                .where(*conditions)
            )
        ).scalar() or 0

        rows = (
            await self.session.execute(
                base.order_by(desc(Reservation.created_at), Reservation.id)
                .offset(skip)
                .limit(min(limit, MAX_ROWS))
            )
        ).all()

        return {
            "total": int(total),
            "items": [
                {
                    "id": str(r.id),
                    "reservation_number": r.reservation_number,
                    "hotel_id": str(hotel.id),
                    "hotel_name": hotel.name,
                    "guest_name": f"{g.first_name or ''} {g.last_name or ''}".strip(),
                    "room_number": room.room_number,
                    "booking_type": r.booking_type,
                    "check_in_date": r.check_in_date.isoformat(),
                    "check_out_date": r.check_out_date.isoformat(),
                    "status": r.status,
                    "payment_status": r.payment_status,
                    "total_amount": float(r.total_amount or 0),
                    "paid_amount": float(r.paid_amount or 0),
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r, g, room, hotel in rows
            ],
        }

    # ----------------------------------------------------------- pul --

    async def finance(
        self, date_from: date, date_to: date, hotel_id: UUID | None = None
    ) -> dict:
        """Mehmonxonalar kesimida tushum va xarajat.

        Yig'indi bazada hisoblanadi: panel bir necha mehmonxonaning
        barcha to'lovini brauzerga tortib kelmasligi kerak.
        """
        pay_conditions = [
            Payment.payment_date >= date_from,
            Payment.payment_date <= date_to,
        ]
        exp_conditions = [
            Expense.expense_date >= date_from,
            Expense.expense_date <= date_to,
        ]
        if hotel_id:
            pay_conditions.append(Payment.hotel_id == hotel_id)
            exp_conditions.append(Expense.hotel_id == hotel_id)

        payments = {
            row[0]: (float(row[1] or 0), int(row[2] or 0))
            for row in (
                await self.session.execute(
                    select(
                        Payment.hotel_id,
                        func.coalesce(func.sum(Payment.amount), 0),
                        func.count(Payment.id),
                    )
                    .where(*pay_conditions)
                    .group_by(Payment.hotel_id)
                )
            ).all()
        }
        expenses = {
            row[0]: float(row[1] or 0)
            for row in (
                await self.session.execute(
                    select(
                        Expense.hotel_id,
                        func.coalesce(func.sum(Expense.amount), 0),
                    )
                    .where(*exp_conditions)
                    .group_by(Expense.hotel_id)
                )
            ).all()
        }

        hotels = (
            (await self.session.execute(select(Hotel).order_by(Hotel.name)))
            .scalars()
            .all()
        )
        rows = []
        for hotel in hotels:
            income, count = payments.get(hotel.id, (0.0, 0))
            expense = expenses.get(hotel.id, 0.0)
            # Hech qanday harakat bo'lmagan mehmonxona ro'yxatni
            # uzaytirib turmasin
            if not income and not expense and not count:
                continue
            rows.append(
                {
                    "hotel_id": str(hotel.id),
                    "hotel_name": hotel.name,
                    "income": income,
                    "expense": expense,
                    "net": income - expense,
                    "payment_count": count,
                }
            )

        return {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "items": rows,
            "income": sum(r["income"] for r in rows),
            "expense": sum(r["expense"] for r in rows),
            "net": sum(r["net"] for r in rows),
        }

    # -------------------------------------------------------- xonalar --

    async def rooms(self, hotel_id: UUID) -> list[dict]:
        rows = (
            await self.session.execute(
                select(Room, Floor, RoomType)
                .join(Floor, Floor.id == Room.floor_id)
                .join(RoomType, RoomType.id == Room.room_type_id)
                .where(Room.hotel_id == hotel_id, Room.is_deleted.is_(False))
                .order_by(Floor.floor_number, Room.room_number)
            )
        ).all()
        return [
            {
                "id": str(room.id),
                "room_number": room.room_number,
                "floor": floor.floor_number,
                "room_type": room_type.name,
                "base_price": float(room.base_price or 0),
                "capacity": room.capacity,
                "status": room.current_status,
            }
            for room, floor, room_type in rows
        ]

    # ------------------------------------------------- harakatlar tarixi --

    async def audit(
        self,
        *,
        hotel_id: UUID | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Kim nima o'zgartirgani — oxirgi yozuvlar.

        Panel egasiga nazorat uchun: mehmonxona ichidan ko'rinmaydigan
        savolga ("kim o'chirdi?") javob shu yerda.
        """
        conditions = []
        if hotel_id:
            conditions.append(AuditLog.hotel_id == hotel_id)
        if action:
            conditions.append(AuditLog.action.ilike(f"%{action.strip()}%"))

        rows = (
            await self.session.execute(
                select(AuditLog, User, Hotel)
                .join(User, User.id == AuditLog.user_id, isouter=True)
                .join(Hotel, Hotel.id == AuditLog.hotel_id, isouter=True)
                .where(*conditions)
                .order_by(desc(AuditLog.created_at))
                .limit(min(limit, MAX_ROWS))
            )
        ).all()
        return [
            {
                "id": str(log.id),
                "action": log.action,
                "entity_type": log.entity_type,
                "entity_id": str(log.entity_id) if log.entity_id else None,
                "hotel_name": hotel.name if hotel else None,
                "user_name": (
                    f"{user.first_name or ''} {user.last_name or ''}".strip()
                    if user
                    else None
                ),
                "ip_address": log.ip_address,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log, user, hotel in rows
        ]

    # ------------------------------------------------------- mehmonlar --

    async def guests(self, search: str | None = None, limit: int = 50) -> list[dict]:
        """Mehmonlar bazasi GLOBAL — barcha mehmonxonalar uchun bitta."""
        stmt = select(Guest)
        text = (search or "").strip()
        if text:
            like = f"%{text}%"
            stmt = stmt.where(
                or_(
                    Guest.first_name.ilike(like),
                    Guest.last_name.ilike(like),
                    Guest.phone.ilike(like),
                    Guest.passport_number.ilike(like),
                )
            )
        rows = (
            (
                await self.session.execute(
                    stmt.order_by(desc(Guest.created_at)).limit(min(limit, MAX_ROWS))
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": str(g.id),
                "name": f"{g.first_name or ''} {g.last_name or ''}".strip(),
                "phone": g.phone,
                "passport_number": g.passport_number,
                "blacklisted": g.blacklisted_at is not None,
                "blacklist_reason": g.blacklist_reason,
                "created_at": g.created_at.isoformat() if g.created_at else None,
            }
            for g in rows
        ]


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Kunning boshi va oxiri — vaqt ustunlari bo'yicha filtr uchun."""
    return datetime.combine(day, time.min), datetime.combine(day, time.max)
