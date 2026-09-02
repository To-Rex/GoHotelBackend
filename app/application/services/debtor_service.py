"""Qarzdorlar — to'lovi tugallanmagan bronlar.

"Qarzdor" deganda XONADAN FOYDALANGAN, lekin pulini to'liq to'lamagan
mehmon tushuniladi: kirgan yoki chiqib ketgan bronlar. Kelgusidagi
tasdiqlangan bron hali qarz emas — mehmon kelmagan ham bo'lishi mumkin va
uni qarzdorlar ro'yxatiga qo'shish xodimni chalg'itardi.

Bekor qilingan va kelmagan bronlar ham kirmaydi: ular bo'yicha xizmat
ko'rsatilmagan, pul qaytarilgan bo'lsa esa hisob-faktura VOID qilingan.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.user import User

#: Qarz hisoblanadigan holatlar
DEBT_STATUSES = ("CHECKED_IN", "CHECKED_OUT")

#: Yaxlitlash xatosi tufayli "1 so'mlik qarz" chiqmasligi uchun
MIN_DEBT = 0.01


class DebtorService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_debtors(
        self,
        hotel_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        created_by: UUID | None = None,
        guest_id: UUID | None = None,
    ) -> dict:
        """To'lovi tugallanmagan bronlar va ular bo'yicha jamlanma.

        `created_by` — xodimning o'z hisobotida faqat o'zi ochgan bronlar
        ko'rinishi uchun. `guest_id` — bitta mehmon bo'yicha.
        """
        stmt = (
            select(
                Reservation,
                Guest.first_name,
                Guest.last_name,
                Guest.phone,
                Room.room_number,
                Branch.name.label("branch_name"),
                User.first_name.label("creator_first"),
                User.last_name.label("creator_last"),
            )
            .join(Guest, Guest.id == Reservation.guest_id, isouter=True)
            .join(Room, Room.id == Reservation.room_id, isouter=True)
            .join(Branch, Branch.id == Reservation.branch_id, isouter=True)
            .join(User, User.id == Reservation.created_by, isouter=True)
            .where(
                Reservation.hotel_id == hotel_id,
                Reservation.is_deleted.is_(False),
                Reservation.status.in_(DEBT_STATUSES),
                # Qarz — to'lanmagan qoldiq
                Reservation.total_amount > Reservation.paid_amount + MIN_DEBT,
            )
            # Eng eski qarz yuqorida: u ko'proq e'tibor talab qiladi
            .order_by(Reservation.check_out_date.asc())
        )
        if date_from is not None:
            stmt = stmt.where(Reservation.check_out_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(Reservation.check_out_date <= date_to)
        if created_by is not None:
            stmt = stmt.where(Reservation.created_by == created_by)
        if guest_id is not None:
            stmt = stmt.where(Reservation.guest_id == guest_id)

        rows = (await self.session.execute(stmt)).all()

        def full_name(first, last) -> str | None:
            return " ".join(p for p in (first, last) if p).strip() or None

        items = []
        total_debt = 0.0
        by_guest: dict[UUID | None, dict] = {}

        for row in rows:
            res = row[0]
            debt = round(float(res.total_amount or 0) - float(res.paid_amount or 0), 2)
            total_debt += debt

            guest_name = full_name(row.first_name, row.last_name)
            items.append(
                {
                    "id": res.id,
                    "reservation_number": res.reservation_number,
                    "guest_id": res.guest_id,
                    "guest_name": guest_name,
                    "guest_phone": row.phone,
                    "room_number": row.room_number,
                    "branch_name": row.branch_name,
                    "booking_type": res.booking_type,
                    "check_in_date": res.check_in_date,
                    "check_out_date": res.check_out_date,
                    "status": res.status,
                    "total_amount": float(res.total_amount or 0),
                    "paid_amount": float(res.paid_amount or 0),
                    "debt_amount": debt,
                    "created_by": res.created_by,
                    "created_by_name": full_name(row.creator_first, row.creator_last),
                }
            )

            # Mehmonlar sahifasi uchun: bitta mehmonning bir nechta qarzi
            # bitta qatorga yig'iladi
            key = res.guest_id
            card = by_guest.setdefault(
                key,
                {
                    "guest_id": key,
                    "guest_name": guest_name,
                    "guest_phone": row.phone,
                    "reservations": 0,
                    "debt_amount": 0.0,
                    "oldest_check_out": res.check_out_date,
                },
            )
            card["reservations"] += 1
            card["debt_amount"] = round(card["debt_amount"] + debt, 2)
            if res.check_out_date and (
                card["oldest_check_out"] is None
                or res.check_out_date < card["oldest_check_out"]
            ):
                card["oldest_check_out"] = res.check_out_date

        guests = sorted(
            by_guest.values(), key=lambda g: g["debt_amount"], reverse=True
        )

        return {
            "summary": {
                "count": len(items),
                "guests": len(by_guest),
                "total_debt": round(total_debt, 2),
            },
            "items": items,
            "guests": guests,
        }
