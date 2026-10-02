"""Moliya sahifasining yig'ma ko'rsatkichlari — bitta yengil so'rovda.

Ilgari bu raqamlar mijoz tomonida hisoblanardi: sahifa davrdagi BARCHA
to'lovlar, hisob-fakturalar, xarajatlar va do'kon sotuvlarini yuklab olib,
ularni brauzerda qo'shib chiqardi. Bunda ikkita muammo bor edi.

Birinchisi — NOTO'G'RI RAQAM. `/finance/invoices` bir so'rovda ko'pi bilan
500 ta hujjat qaytaradi (`limit` chegarasi qat'iy), do'kon sotuvlari esa
1000 ta. Davrda shundan ko'p yozuv bo'lsa qolganlari umuman kelmasdi va
"Hisob-fakturalar", "To'langan", "Qarzdorlik" kartalari jimgina kam
ko'rsatardi — moliya sahifasi uchun bu eng yomon nosozlik turi, chunki
xato ko'rinmaydi.

Ikkinchisi — OG'IRLIK. Bir necha ming yozuvni JSON qilib uzatish va
brauzerda aylanib chiqish sahifani sekinlashtiradi. Endi jadvallar
sahifalab olinadi, ya'ni to'liq ro'yxat umuman kelmaydi — demak
yig'indini baribir shu yerda hisoblash kerak.

Formulalar mijozdagi eski hisob-kitobning AYNAN o'zi: sahifadagi raqamlar
o'zgarmasligi kerak, faqat qayerda hisoblangani o'zgardi.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.expense import Expense
from app.infrastructure.database.models.invoice import Invoice
from app.infrastructure.database.models.payment import Payment
from app.infrastructure.database.models.shop import ShopSale
from app.infrastructure.database.models.user import User

#: Bekor qilingan va qaytarilgan hujjatlar qarzdorlikka kirmaydi —
#: ular bo'yicha olinadigan pul qolmagan.
NON_DEBT_STATUSES = ("VOID", "REFUNDED")

#: Usuli ko'rsatilmagan pul harakati ham ko'rinishi kerak: aks holda summa
#: jadvalda yo'qolib qolgandek tuyuladi.
UNKNOWN_METHOD = "UNKNOWN"

#: To'langan do'kon savdosi tushumga kiradi, `PENDING` esa bronga yozilgan
#: qarz bo'lib qoladi.
SHOP_PAID = "PAID"
SHOP_PENDING = "PENDING"

#: Kunlik qatorning eng uzun davri — bir yil (kabisa bilan)
MAX_DAILY_DAYS = 366

#: Xodimlar kesimidagi hisobotni ko'ra oladiganlar (admin doim). Bu boshqa
#: xodimlar qancha pul olganini ochib beradi, shuning uchun `/finance/summary`
#: dan torroq: moliya yoki kassa nazorati huquqi kerak.
STAFF_REPORT_CODES = ("finance.view", "shift.force_close", "report.view")


def can_view_staff_report(current_user: dict) -> bool:
    if current_user.get("user_type") in ("ADMIN", "SUPER_ADMIN"):
        return True
    codes = current_user.get("permissions") or []
    return any(code in codes for code in STAFF_REPORT_CODES)


def _num(value) -> float:
    """`Decimal | None` ni floatga keltiradi."""
    return float(value or 0)


def _method_key(method: str | None) -> str:
    return (method or "").upper() or UNKNOWN_METHOD


class FinanceSummaryService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def build(
        self,
        hotel_id: UUID | None,
        date_from: date | None = None,
        date_to: date | None = None,
        status: str | None = None,
    ) -> dict:
        """Davr bo'yicha barcha yig'ma ko'rsatkichlar.

        `status` faqat hisob-fakturalarga tegishli: sahifada holat filtri
        tanlanganda kartalar ham o'sha to'plam bo'yicha hisoblanadi —
        mijoz tomonida ham aynan shunday bo'lgan.
        """
        # Usullar jadvali uch manbadan yig'iladi: bron to'lovlari, do'kon
        # savdosi va xarajatlar
        methods: dict[str, dict[str, float]] = {}

        def bucket(method: str | None) -> dict[str, float]:
            key = _method_key(method)
            if key not in methods:
                methods[key] = {"pay": 0.0, "shop": 0.0, "expense": 0.0}
            return methods[key]

        payments = await self._payments(hotel_id, date_from, date_to, bucket)
        invoices = await self._invoices(hotel_id, date_from, date_to, status)
        expenses = await self._expenses(hotel_id, date_from, date_to, bucket)
        shop = await self._shop(hotel_id, date_from, date_to, bucket)
        # Davrda yozilgan faol jarimalar (kech chiqish, shikast) — yangi
        # maydonlar, mavjudlari o'zgarmaydi
        from app.application.services.reservation_penalty_service import (
            penalty_totals,
        )

        penalties = await penalty_totals(self.session, hotel_id, date_from, date_to)

        return {
            **payments,
            **invoices,
            **expenses,
            **shop,
            **penalties,
            "methods": [{"key": key, **values} for key, values in methods.items()],
        }

    # --------------------------------------------- xodimlar kesimida --

    async def by_staff(
        self,
        hotel_id: UUID | None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict:
        """Davr tushumi KIM tomonidan olingan — xodimlar kesimida.

        Pul har doim yozuvning o'zidan bog'lanadi: to'lov — `Payment.created_by`,
        do'kon savdosi — `ShopSale.created_by`, xarajat — `Expense.created_by`
        ("Mening hisobotim" va smena kassasi bilan bir xil ta'rif). Sana
        chegaralari `build` bilan AYNAN bir xil, ya'ni xodimlar bo'yicha
        tushum yig'indisi "Jami tushum" (bron to'lovlari + do'kon) ga teng.
        """
        rows: dict = {}

        def row(user_id) -> dict:
            if user_id not in rows:
                rows[user_id] = {
                    "user_id": str(user_id) if user_id else None,
                    "income": 0.0,
                    "payment_count": 0,
                    "refunds": 0.0,
                    "shop": 0.0,
                    "shop_count": 0,
                    "expense": 0.0,
                    "expense_count": 0,
                    "cash_expense": 0.0,
                    "methods": {},
                }
            return rows[user_id]

        def method(r: dict, key: str | None) -> dict:
            return r["methods"].setdefault(_method_key(key), {"pay": 0.0, "shop": 0.0})

        # Bron to'lovlari (qaytarimlar manfiy — tushumni kamaytiradi)
        pay = select(
            Payment.created_by,
            Payment.payment_method,
            func.coalesce(func.sum(Payment.amount), 0),
            func.count(Payment.id),
            func.coalesce(
                func.sum(case((Payment.amount < 0, -Payment.amount), else_=0)), 0
            ),
        )
        if hotel_id is not None:
            pay = pay.where(Payment.hotel_id == hotel_id)
        if date_from:
            pay = pay.where(Payment.payment_date >= date_from)
        if date_to:
            pay = pay.where(Payment.payment_date <= date_to)
        for user_id, pay_method, total, count, refunds in (
            await self.session.execute(
                pay.group_by(Payment.created_by, Payment.payment_method)
            )
        ).all():
            r = row(user_id)
            r["income"] += _num(total)
            r["payment_count"] += int(count or 0)
            r["refunds"] += _num(refunds)
            method(r, pay_method)["pay"] += _num(total)

        # Do'kon: to'langan savdo, TO'LOV sanasi bo'yicha (build bilan bir xil)
        shop = select(
            ShopSale.created_by,
            ShopSale.total_amount,
            ShopSale.payment_method,
            ShopSale.payments,
        ).where(ShopSale.status == SHOP_PAID)
        if hotel_id is not None:
            shop = shop.where(ShopSale.hotel_id == hotel_id)
        if date_from:
            shop = shop.where(ShopSale.paid_at >= datetime.combine(date_from, time.min))
        if date_to:
            shop = shop.where(ShopSale.paid_at <= datetime.combine(date_to, time.max))
        for user_id, total_amount, sale_method, parts in (
            await self.session.execute(shop)
        ).all():
            r = row(user_id)
            r["shop"] += _num(total_amount)
            r["shop_count"] += 1
            if parts:
                for part in parts:
                    method(r, part.get("payment_method"))["shop"] += _num(part.get("amount"))
            else:
                method(r, sale_method)["shop"] += _num(total_amount)

        # Xarajatlar — kim kassadan pul chiqargani
        exp = select(
            Expense.created_by,
            Expense.payment_method,
            func.coalesce(func.sum(Expense.amount), 0),
            func.count(Expense.id),
        )
        if hotel_id is not None:
            exp = exp.where(Expense.hotel_id == hotel_id)
        if date_from:
            exp = exp.where(Expense.expense_date >= date_from)
        if date_to:
            exp = exp.where(Expense.expense_date <= date_to)
        for user_id, exp_method, total, count in (
            await self.session.execute(
                exp.group_by(Expense.created_by, Expense.payment_method)
            )
        ).all():
            r = row(user_id)
            r["expense"] += _num(total)
            r["expense_count"] += int(count or 0)
            if _method_key(exp_method) == "CASH":
                r["cash_expense"] += _num(total)

        ids = [uid for uid in rows if uid is not None]
        people: dict = {}
        if ids:
            for uid, first, last, user_type, status in (
                await self.session.execute(
                    select(
                        User.id, User.first_name, User.last_name, User.user_type, User.status
                    ).where(User.id.in_(ids))
                )
            ).all():
                people[uid] = (
                    f"{first or ''} {last or ''}".strip() or None,
                    user_type,
                    status,
                )

        items = []
        for uid, r in rows.items():
            name, user_type, status = people.get(uid, (None, None, None))
            methods = r.pop("methods")
            cash = methods.get("CASH", {"pay": 0.0, "shop": 0.0})
            items.append(
                {
                    **r,
                    "name": name,
                    "user_type": user_type,
                    "status": status,
                    "revenue": r["income"] + r["shop"],
                    "cash": cash["pay"] + cash["shop"],
                    "methods": [{"key": k, **v} for k, v in methods.items()],
                }
            )
        # Eng ko'p tushum olgan birinchi; teng bo'lsa — ko'proq xarajat
        # qilgan, keyin ism bo'yicha (tartib har safar bir xil bo'lsin)
        items.sort(key=lambda i: (-i["revenue"], -i["expense"], i["name"] or ""))

        return {
            "items": items,
            "total": {
                "revenue": sum(i["revenue"] for i in items),
                "income": sum(i["income"] for i in items),
                "shop": sum(i["shop"] for i in items),
                "refunds": sum(i["refunds"] for i in items),
                "expense": sum(i["expense"] for i in items),
                "payment_count": sum(i["payment_count"] for i in items),
            },
        }

    # ------------------------------------------------- kunlik qatorlar --

    async def daily(self, hotel_id: UUID | None, date_from: date, date_to: date) -> list[dict]:
        """Davrning HAR KUNI uchun: bron tushumi, to'lovlar soni, qaytarim,
        xarajat va do'kon savdosi — grafik uchun bitta so'rovda.

        Kun ta'rifi `build` bilan AYNAN bir xil (to'lov — `payment_date`,
        xarajat — `expense_date`, do'kon — to'langan kun), ya'ni kunlar
        yig'indisi davr yig'masiga teng bo'ladi. Harakat bo'lmagan kun ham
        qatorga kiradi (nollar bilan) — grafikda bo'shliq bo'lmasin.
        """
        days: dict[date, dict] = {}
        cursor = date_from
        while cursor <= date_to:
            days[cursor] = {
                "date": cursor.isoformat(),
                "income": 0.0,
                "payment_count": 0,
                "refunds": 0.0,
                "expense": 0.0,
                "shop": 0.0,
            }
            cursor += timedelta(days=1)

        pay = select(
            Payment.payment_date,
            func.coalesce(func.sum(Payment.amount), 0),
            func.count(Payment.id),
            func.coalesce(
                func.sum(case((Payment.amount < 0, -Payment.amount), else_=0)), 0
            ),
        ).where(Payment.payment_date >= date_from, Payment.payment_date <= date_to)
        if hotel_id is not None:
            pay = pay.where(Payment.hotel_id == hotel_id)
        for day, total, count, refunds in (
            await self.session.execute(pay.group_by(Payment.payment_date))
        ).all():
            row = days.get(day)
            if row is not None:
                row["income"] += _num(total)
                row["payment_count"] += int(count or 0)
                row["refunds"] += _num(refunds)

        exp = select(
            Expense.expense_date, func.coalesce(func.sum(Expense.amount), 0)
        ).where(Expense.expense_date >= date_from, Expense.expense_date <= date_to)
        if hotel_id is not None:
            exp = exp.where(Expense.hotel_id == hotel_id)
        for day, total in (
            await self.session.execute(exp.group_by(Expense.expense_date))
        ).all():
            row = days.get(day)
            if row is not None:
                row["expense"] += _num(total)

        paid_day = func.date(ShopSale.paid_at)
        shop = select(paid_day, func.coalesce(func.sum(ShopSale.total_amount), 0)).where(
            ShopSale.status == SHOP_PAID,
            ShopSale.paid_at >= datetime.combine(date_from, time.min),
            ShopSale.paid_at <= datetime.combine(date_to, time.max),
        )
        if hotel_id is not None:
            shop = shop.where(ShopSale.hotel_id == hotel_id)
        for day, total in (await self.session.execute(shop.group_by(paid_day))).all():
            if isinstance(day, datetime):
                day = day.date()
            row = days.get(day)
            if row is not None:
                row["shop"] += _num(total)

        return list(days.values())

    # ------------------------------------------------------ to'lovlar --

    async def _payments(self, hotel_id, date_from, date_to, bucket) -> dict:
        def scoped(stmt):
            if hotel_id is not None:
                stmt = stmt.where(Payment.hotel_id == hotel_id)
            if date_from:
                stmt = stmt.where(Payment.payment_date >= date_from)
            if date_to:
                stmt = stmt.where(Payment.payment_date <= date_to)
            return stmt

        income, count, refunds = (
            await self.session.execute(
                scoped(
                    select(
                        func.coalesce(func.sum(Payment.amount), 0),
                        func.count(Payment.id),
                        # Qaytarimlar manfiy to'lov bo'lib yoziladi: ular
                        # tushumni o'zi kamaytiradi, lekin alohida ham
                        # ko'rsatiladi — aks holda "tushum nega kamaydi"
                        # degan savol javobsiz qoladi
                        func.coalesce(
                            func.sum(
                                case((Payment.amount < 0, -Payment.amount), else_=0)
                            ),
                            0,
                        ),
                    )
                )
            )
        ).one()

        for method, total in (
            await self.session.execute(
                scoped(
                    select(
                        Payment.payment_method,
                        func.coalesce(func.sum(Payment.amount), 0),
                    )
                ).group_by(Payment.payment_method)
            )
        ).all():
            bucket(method)["pay"] += _num(total)

        return {
            "income": _num(income),
            "payment_count": int(count or 0),
            "refunds": _num(refunds),
        }

    # ------------------------------------------------ hisob-fakturalar --

    async def _invoices(self, hotel_id, date_from, date_to, status) -> dict:
        stmt = select(
            func.coalesce(func.sum(Invoice.total_amount), 0),
            func.coalesce(func.sum(Invoice.discount_amount), 0),
            func.coalesce(func.sum(Invoice.paid_amount), 0),
            func.count(Invoice.id),
            # Qoldiq hech qachon manfiy bo'lmaydi: ortiqcha to'langan hujjat
            # boshqasining qarzini yopib yubormasligi kerak
            func.coalesce(
                func.sum(
                    case(
                        (
                            Invoice.status.notin_(NON_DEBT_STATUSES),
                            func.greatest(
                                Invoice.total_amount - Invoice.paid_amount, 0
                            ),
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        )
        if hotel_id is not None:
            stmt = stmt.where(Invoice.hotel_id == hotel_id)
        if status:
            stmt = stmt.where(Invoice.status == status)
        if date_from:
            stmt = stmt.where(Invoice.invoice_date >= date_from)
        if date_to:
            stmt = stmt.where(Invoice.invoice_date <= date_to)

        total, discount, paid, count, debt = (await self.session.execute(stmt)).one()
        return {
            "invoice_total": _num(total),
            "invoice_discount": _num(discount),
            "invoice_paid": _num(paid),
            "invoice_count": int(count or 0),
            "debt": _num(debt),
        }

    # ------------------------------------------------------ xarajatlar --

    async def _expenses(self, hotel_id, date_from, date_to, bucket) -> dict:
        def scoped(stmt):
            if hotel_id is not None:
                stmt = stmt.where(Expense.hotel_id == hotel_id)
            if date_from:
                stmt = stmt.where(Expense.expense_date >= date_from)
            if date_to:
                stmt = stmt.where(Expense.expense_date <= date_to)
            return stmt

        total, count = (
            await self.session.execute(
                scoped(
                    select(
                        func.coalesce(func.sum(Expense.amount), 0),
                        func.count(Expense.id),
                    )
                )
            )
        ).one()

        for method, amount in (
            await self.session.execute(
                scoped(
                    select(
                        Expense.payment_method,
                        func.coalesce(func.sum(Expense.amount), 0),
                    )
                ).group_by(Expense.payment_method)
            )
        ).all():
            bucket(method)["expense"] += _num(amount)

        # Toifalar: bazada bo'sh yoki faqat probeldan iborat nom uchraydi,
        # ular mijozdagidek "Boshqa" ga yig'iladi — shuning uchun guruhlash
        # natijasi Pythonda birlashtiriladi
        categories: dict[str, dict[str, float]] = {}
        for name, amount, cnt in (
            await self.session.execute(
                scoped(
                    select(
                        Expense.category,
                        func.coalesce(func.sum(Expense.amount), 0),
                        func.count(Expense.id),
                    )
                ).group_by(Expense.category)
            )
        ).all():
            key = (name or "").strip() or "Boshqa"
            row = categories.setdefault(key, {"total": 0.0, "count": 0})
            row["total"] += _num(amount)
            row["count"] += int(cnt or 0)

        return {
            "expense_total": _num(total),
            "expense_count": int(count or 0),
            "expense_categories": sorted(
                ({"name": k, **v} for k, v in categories.items()),
                key=lambda c: c["total"],
                reverse=True,
            ),
        }

    # ---------------------------------------------------------- do'kon --

    async def _shop(self, hotel_id, date_from, date_to, bucket) -> dict:
        # To'langan savdo TO'LOV sanasi bo'yicha olinadi: bronga yozilib
        # keyin to'langan sotuv aynan to'langan kun tushumiga tushadi
        paid = select(
            ShopSale.total_amount, ShopSale.payment_method, ShopSale.payments
        ).where(ShopSale.status == SHOP_PAID)
        if hotel_id is not None:
            paid = paid.where(ShopSale.hotel_id == hotel_id)
        if date_from:
            paid = paid.where(ShopSale.paid_at >= datetime.combine(date_from, time.min))
        if date_to:
            paid = paid.where(ShopSale.paid_at <= datetime.combine(date_to, time.max))

        revenue = 0.0
        paid_count = 0
        for total_amount, method, parts in (await self.session.execute(paid)).all():
            revenue += _num(total_amount)
            paid_count += 1
            # Bo'lib to'langan savdo har bo'lagi o'z usuliga yoziladi —
            # jami "MIXED" bo'lib qolmasligi uchun
            if parts:
                for part in parts:
                    bucket(part.get("payment_method"))["shop"] += _num(
                        part.get("amount")
                    )
            else:
                bucket(method)["shop"] += _num(total_amount)

        # Bronga yozilgan to'lanmagan savdo — JORIY qoldiq, davrga bog'liq
        # emas, shuning uchun sana filtri qo'llanmaydi
        debts = select(
            func.coalesce(func.sum(ShopSale.total_amount), 0),
            func.count(ShopSale.id),
        ).where(ShopSale.status == SHOP_PENDING)
        if hotel_id is not None:
            debts = debts.where(ShopSale.hotel_id == hotel_id)
        debt_total, debt_count = (await self.session.execute(debts)).one()

        return {
            "shop_revenue": revenue,
            "shop_paid_count": paid_count,
            "shop_debt": _num(debt_total),
            "shop_debt_count": int(debt_count or 0),
        }
