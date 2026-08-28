"""Xodimning shaxsiy hisoboti — "Mening hisobotim" sahifasi uchun.

Bu xizmat bitta savolga javob beradi: TANLANGAN KUNLARDA shu xodim nima qildi.

Eng muhim qaror — pulni kim bilan bog'lash. Ilgari sahifa xodim YARATGAN
bronlarning `paid_amount` yig'indisini "qabul qilingan to'lovlar" deb
ko'rsatardi. `paid_amount` esa bronning butun umri bo'yicha to'langan summa va
u kim to'laganini bilmaydi: keyingi to'lovni kim kiritsa, o'sha yozib ketadi.
Natijada kechki xodim bron yaratib, ertalab boshqasi qolgan pulni olsa, pul
bronni yaratganga yozilardi; o'tgan haftagi bronga bugun olingan pul esa
umuman hech kimning bugungi hisobotiga tushmasdi.

Shuning uchun bu yerda pul HAR DOIM to'lovning o'zidan olinadi
(`Payment.created_by`, `ShopSale.created_by`, `Expense.created_by`) — ya'ni
kassa hisobidagi (`shift_service.cash_breakdown`) ta'rif bilan bir xil. Bron
soni va summasi esa alohida turadi: ular MUALLIFLIK ko'rsatkichi, pul emas.

Sana chegaralari mahalliy kun bo'yicha (APP_TZ_OFFSET_MINUTES): xodim uchun
"bugun" — bu uning ish kuni, UTC sutkasi emas.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.infrastructure.database.models.expense import Expense
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.payment import Payment
from app.infrastructure.database.models.reservation import Reservation
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.shop import ShopSale

#: Hisobotda alohida ko'rsatiladigan to'lov turlari
METHODS = ("CASH", "CARD", "TRANSFER")


def _dec(value) -> Decimal:
    return Decimal(str(value or 0))


def local_day_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """Mahalliy kunlar oralig'ini UTC timestamp oralig'iga aylantiradi.

    Ustunlar UTC saqlanadi, xodim esa mahalliy kun bilan o'ylaydi. Bu
    almashtirilmasa, O'zbekistonda soat 19:00 dan keyingi har bir amal ertangi
    kunga tushib qolardi.
    """
    offset = timedelta(minutes=settings.APP_TZ_OFFSET_MINUTES)
    start = datetime.combine(date_from, time.min).replace(tzinfo=timezone.utc) - offset
    end = datetime.combine(date_to, time.max).replace(tzinfo=timezone.utc) - offset
    return start, end


def _empty_methods() -> dict:
    return {method.lower(): Decimal("0") for method in METHODS}


def _money(bucket: dict) -> dict:
    return {key: float(value) for key, value in bucket.items()}


class PersonalReportService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def summary(
        self,
        hotel_id: UUID | None,
        user_id: UUID,
        date_from: date,
        date_to: date,
    ) -> dict:
        start, end = local_day_bounds(date_from, date_to)

        reservations = await self._reservations(hotel_id, user_id, start, end)
        payments = await self._payments(hotel_id, user_id, start, end)
        shop = await self._shop(hotel_id, user_id, start, end)
        expenses = await self._expenses(hotel_id, user_id, date_from, date_to)

        # Kassaga tushgan sof naqd — kassa hisobidagi mantiq bilan bir xil
        net_cash = (
            _dec(payments["by_method"]["cash"])
            + _dec(shop["by_method"]["cash"])
            - _dec(expenses["by_method"]["cash"])
        )

        return {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "reservations": reservations,
            "payments": payments,
            "shop": shop,
            "expenses": expenses,
            "net_cash": float(net_cash),
        }

    # -------------------------------------------------------------- bronlar

    async def _reservations(self, hotel_id, user_id, start, end) -> dict:
        """Xodim YARATGAN bronlar — mualliflik ko'rsatkichi, pul emas.

        Bekor qilinganlar alohida sanaladi, lekin ro'yxatdan chiqarilmaydi:
        ular bo'yicha olingan pul to'lovlar bo'limida o'z o'rnida turadi.
        """
        stmt = (
            select(Reservation, Guest, Room)
            .outerjoin(Guest, Guest.id == Reservation.guest_id)
            .outerjoin(Room, Room.id == Reservation.room_id)
            .where(
                Reservation.created_by == user_id,
                Reservation.is_deleted.is_(False),
                Reservation.created_at >= start,
                Reservation.created_at <= end,
            )
            .order_by(Reservation.created_at.desc())
        )
        if hotel_id is not None:
            stmt = stmt.where(Reservation.hotel_id == hotel_id)

        rows = (await self.session.execute(stmt)).all()
        total = Decimal("0")
        cancelled = 0
        items = []
        for reservation, guest, room in rows:
            if reservation.status == "CANCELLED":
                cancelled += 1
            else:
                total += _dec(reservation.total_amount)
            items.append(
                {
                    "id": str(reservation.id),
                    "reservation_number": reservation.reservation_number,
                    "guest_name": (
                        f"{guest.first_name or ''} {guest.last_name or ''}".strip()
                        if guest
                        else None
                    ),
                    "room_number": room.room_number if room else None,
                    "status": reservation.status,
                    "total_amount": float(_dec(reservation.total_amount)),
                    "paid_amount": float(_dec(reservation.paid_amount)),
                    "check_in_date": reservation.check_in_date.isoformat()
                    if reservation.check_in_date
                    else None,
                    "check_out_date": reservation.check_out_date.isoformat()
                    if reservation.check_out_date
                    else None,
                    "created_at": reservation.created_at.isoformat(),
                }
            )
        return {
            "count": len(items),
            "cancelled_count": cancelled,
            "total_amount": float(total),
            "items": items,
        }

    # -------------------------------------------------------------- to'lovlar

    async def _payments(self, hotel_id, user_id, start, end) -> dict:
        """Xodim QABUL QILGAN to'lovlar.

        Qaytarimlar manfiy `Payment` bo'lib yoziladi, shuning uchun ular
        yig'indini o'zi kamaytiradi — alohida ayirish shart emas.
        """
        stmt = select(Payment).where(
            Payment.created_by == user_id,
            Payment.created_at >= start,
            Payment.created_at <= end,
        )
        if hotel_id is not None:
            stmt = stmt.where(Payment.hotel_id == hotel_id)

        rows = (await self.session.execute(stmt)).scalars().all()
        by_method = _empty_methods()
        other = Decimal("0")
        total = Decimal("0")
        refunds = Decimal("0")
        for payment in rows:
            amount = _dec(payment.amount)
            total += amount
            if amount < 0:
                refunds += -amount
            key = (payment.payment_method or "").lower()
            if key in by_method:
                by_method[key] += amount
            else:
                other += amount
        return {
            "count": len(rows),
            "total": float(total),
            "refunds": float(refunds),
            "by_method": _money({**by_method, "other": other}),
        }

    # --------------------------------------------------------------- do'kon

    async def _shop(self, hotel_id, user_id, start, end) -> dict:
        """Do'kon savdolari — TO'LANGANLAR pul sifatida, qolganlari alohida.

        Sana `paid_at` bo'yicha: hisobot pul qachon olinganini ko'rsatishi
        kerak, chek qachon yozilganini emas. To'lanmagan savdolar tushumga
        qo'shilmaydi, lekin ko'rinmay ham qolmaydi — ular alohida sanaladi.
        """
        stmt = select(ShopSale).where(ShopSale.created_by == user_id)
        if hotel_id is not None:
            stmt = stmt.where(ShopSale.hotel_id == hotel_id)

        paid_stmt = stmt.where(
            ShopSale.status == "PAID",
            ShopSale.paid_at.is_not(None),
            ShopSale.paid_at >= start,
            ShopSale.paid_at <= end,
        )
        paid_rows = (await self.session.execute(paid_stmt)).scalars().all()

        by_method = _empty_methods()
        other = Decimal("0")
        total = Decimal("0")
        for sale in paid_rows:
            total += _dec(sale.total_amount)
            # Bo'lib to'langan savdoda har bo'lak o'z turiga yoziladi
            if sale.payments:
                for part in sale.payments:
                    key = str(part.get("payment_method") or "").lower()
                    amount = _dec(part.get("amount"))
                    if key in by_method:
                        by_method[key] += amount
                    else:
                        other += amount
            else:
                key = (sale.payment_method or "").lower()
                if key in by_method:
                    by_method[key] += _dec(sale.total_amount)
                else:
                    other += _dec(sale.total_amount)

        unpaid_stmt = stmt.where(
            ShopSale.status != "PAID",
            ShopSale.created_at >= start,
            ShopSale.created_at <= end,
        )
        unpaid_rows = (await self.session.execute(unpaid_stmt)).scalars().all()

        return {
            "count": len(paid_rows),
            "total": float(total),
            "by_method": _money({**by_method, "other": other}),
            "unpaid_count": len(unpaid_rows),
            "unpaid_total": float(sum((_dec(s.total_amount) for s in unpaid_rows), Decimal("0"))),
        }

    # ------------------------------------------------------------ xarajatlar

    async def _expenses(self, hotel_id, user_id, date_from, date_to) -> dict:
        """Xodim kiritgan xarajatlar.

        `expense_date` — DATE ustuni va u xodim tomonidan qo'lda qo'yiladi,
        shuning uchun u mahalliy sana bilan to'g'ridan-to'g'ri solishtiriladi
        (vaqt mintaqasi almashtirish bu yerda o'rinsiz).
        """
        stmt = select(Expense).where(
            Expense.created_by == user_id,
            Expense.expense_date >= date_from,
            Expense.expense_date <= date_to,
        )
        if hotel_id is not None:
            stmt = stmt.where(Expense.hotel_id == hotel_id)

        rows = (await self.session.execute(stmt)).scalars().all()
        by_method = _empty_methods()
        other = Decimal("0")
        total = Decimal("0")
        items = []
        for expense in rows:
            amount = _dec(expense.amount)
            total += amount
            key = (expense.payment_method or "").lower()
            if key in by_method:
                by_method[key] += amount
            else:
                other += amount
            items.append(
                {
                    "id": str(expense.id),
                    "title": expense.title,
                    "category": expense.category,
                    "notes": expense.notes,
                    "amount": float(amount),
                    "payment_method": expense.payment_method,
                    "expense_date": expense.expense_date.isoformat()
                    if expense.expense_date
                    else None,
                }
            )
        items.sort(key=lambda item: item["expense_date"] or "", reverse=True)
        return {
            "count": len(items),
            "total": float(total),
            "by_method": _money({**by_method, "other": other}),
            "items": items,
        }
