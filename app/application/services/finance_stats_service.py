"""Moliya → To'lovlar va Hisob-fakturalar bo'limlarining statistikasi.

Ikkalasi ham ro'yxat bilan BIR XIL shartlardan hisoblanadi: davr, qidiruv
va bo'lim filtrlari (`FinanceService._feed_query`, `_invoice_conditions`).
Xodim jadvalda "Jarima" ni tanlasa — statistika ham faqat jarima
to'lovlari bo'yicha; filtrsiz — butun davr.

TO'LOVLAR (`payments_stats`):
* jami (qaytarimlar ayirilgan), kirim va qaytarim alohida, o'rtacha va eng
  katta to'lov;
* to'lov turlari (naqd, karta, online, o'tkazma — eski kodlar o'z turiga);
* NIMA UCHUN: xona, jarima, xizmat, ortiqcha, do'kon, qaytarim — jadvaldagi
  belgilar bilan bir xil taqsimot (`payment_purpose.allocate`); qarz to'lovi
  (mehmon chiqib ketgandan keyin) alohida;
* xodimlar kesimi (pulni kim qabul qilgan), kunlar bo'yicha (grafik uchun)
  va tahrirlangan to'lovlar soni.

HISOB-FAKTURALAR (`invoices_stats`):
* soni, umumiy summa, to'langan, qoldiq (qarz), chegirma, yig'ilish foizi;
* holatlar kesimi; qatorlar tarkibi (xona, jarima, xizmat ...);
* qarz yoshi (hujjat sanasidan beri: 0–7, 8–30, 31–90, 90+ kun) va eng
  katta qarzli hujjatlar. Bekor qilingan / qaytarilgan hujjat qarz emas.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services import payment_purpose as purpose
from app.application.services.finance_service import (
    FinanceService,
    InvoiceFilters,
    PaymentFeedFilters,
    _chunks,
)
from app.infrastructure.database.models.invoice import Invoice, InvoiceLineItem
from app.infrastructure.database.models.payment import Payment
from app.infrastructure.database.models.user import User
from app.shared.utils import hotel_today

#: Kanonik to'lov turlari (eski kodlar o'z turiga)
CANONICAL_METHODS = {
    "CASH": "CASH",
    "CARD": "CARD",
    "CREDIT_CARD": "CARD",
    "DEBIT_CARD": "CARD",
    "ONLINE": "ONLINE",
    "MOBILE_PAYMENT": "ONLINE",
    "BANK_TRANSFER": "BANK_TRANSFER",
    "TRANSFER": "BANK_TRANSFER",
}
METHOD_ORDER = ("CASH", "CARD", "ONLINE", "BANK_TRANSFER", "MIXED", "OTHER")
PURPOSE_ORDER = (
    purpose.KIND_ROOM,
    purpose.KIND_PENALTY,
    purpose.KIND_SERVICE,
    purpose.KIND_OTHER,
    purpose.KIND_EXTRA,
    purpose.KIND_SHOP,
    purpose.KIND_REFUND,
)
#: Hujjat qarz hisoblanmaydigan holatlar
NON_DEBT_STATUSES = ("VOID", "CANCELLED", "REFUNDED")
STATUS_ORDER = ("DRAFT", "ISSUED", "PARTIALLY_PAID", "PAID", "VOID", "CANCELLED", "REFUNDED")
#: Qarz yoshi chegaralari (kun, hujjat sanasidan)
AGING_BUCKETS = ((0, 7, "0_7"), (8, 30, "8_30"), (31, 90, "31_90"), (91, None, "90_plus"))
TOP_DEBTS = 5
TOP_STAFF = 10
EPS = 0.005


def _num(v) -> float:
    return float(v or 0)


def _r(v) -> float:
    return round(float(v or 0), 2)


def canonical_method(code: str | None) -> str:
    key = (code or "").strip().upper()
    if key == "MIXED":
        return "MIXED"
    return CANONICAL_METHODS.get(key, "OTHER")


class FinanceStatsService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.finance = FinanceService(session)

    # ---------------------------------------------------------- to'lovlar --

    async def payments_stats(
        self,
        hotel_id: UUID | None,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        include_shop: bool = False,
        filters: PaymentFeedFilters | None = None,
    ) -> dict:
        f = filters or PaymentFeedFilters()
        feed = self.finance._feed_query(
            hotel_id, date_from, date_to, search, None, include_shop, f
        )
        rows = (
            await self.session.execute(
                select(
                    feed.c.id,
                    feed.c.source,
                    feed.c.number,
                    feed.c.amount,
                    feed.c.method,
                    feed.c.pay_date,
                    feed.c.uid,
                    feed.c.created_at,
                )
            )
        ).all()

        reservation_ids = [r.id for r in rows if r.source == purpose.SOURCE_RESERVATION]
        info = await self.finance._purpose_info(reservation_ids) if reservation_ids else {}
        if f.needs_purpose:
            # "Turi" (xona, jarima, ...) — ro'yxatdagi qoida bilan bir xil
            kept = []
            for r in rows:
                if r.source == purpose.SOURCE_RESERVATION:
                    found = info.get(r.id)
                    if found is None or not purpose.matches_kinds(
                        f.kinds,
                        source=r.source,
                        amount=_num(r.amount),
                        parts=found[0],
                        after_checkout=found[1],
                    ):
                        continue
                kept.append(r)
            rows = kept

        income = refunds = 0.0
        income_count = refund_count = 0
        largest: dict | None = None
        methods: dict[str, list] = defaultdict(lambda: [0.0, 0])
        purposes: dict[str, float] = defaultdict(float)
        sources: dict[str, list] = defaultdict(lambda: [0.0, 0])
        staff: dict[str, list] = defaultdict(lambda: [0.0, 0])
        days: dict[str, list] = defaultdict(lambda: [0.0, 0])
        debt_amount, debt_count = 0.0, 0

        for r in rows:
            amount = _num(r.amount)
            if amount < 0:
                refunds += -amount
                refund_count += 1
            else:
                income += amount
                income_count += 1
                if largest is None or amount > largest["amount"]:
                    largest = {
                        "amount": _r(amount),
                        "number": r.number,
                        "source": r.source,
                        "date": str(r.pay_date) if r.pay_date else None,
                    }
            m = methods[canonical_method(r.method)]
            m[0] += amount
            m[1] += 1
            s = sources[r.source]
            s[0] += amount
            s[1] += 1
            if r.uid is not None:
                st = staff[str(r.uid)]
                st[0] += amount
                st[1] += 1
            if r.pay_date is not None:
                d = days[str(r.pay_date)]
                d[0] += amount
                d[1] += 1

            if r.source == purpose.SOURCE_SHOP:
                purposes[purpose.KIND_SHOP] += amount
            elif amount < 0:
                purposes[purpose.KIND_REFUND] += amount
            else:
                parts, after_checkout = info.get(r.id, ([], False))
                if after_checkout:
                    debt_amount += amount
                    debt_count += 1
                covered = 0.0
                for part in parts or []:
                    part_amount = _num(part.get("amount"))
                    purposes[part.get("kind") or purpose.KIND_OTHER] += part_amount
                    covered += part_amount
                if amount - covered > EPS:
                    # Taqsimlanmagan qoldiq (fakturasiz to'lov) — "boshqa"
                    purposes[purpose.KIND_OTHER] += amount - covered

        # Tahrirlangan (turi o'zgartirilgan) bron to'lovlari
        edited = 0
        kept_reservation = [r.id for r in rows if r.source == purpose.SOURCE_RESERVATION]
        for chunk in _chunks(kept_reservation):
            for (changes,) in (
                await self.session.execute(
                    select(Payment.method_changes).where(Payment.id.in_(chunk))
                )
            ).all():
                if isinstance(changes, list) and any(
                    isinstance(e, dict) and e.get("kind") != "split_from" for e in changes
                ):
                    edited += 1

        names: dict[str, str | None] = {}
        if staff:
            for user in (
                await self.session.execute(
                    select(User).where(User.id.in_([UUID(k) for k in staff]))
                )
            ).scalars():
                names[str(user.id)] = (
                    f"{user.first_name or ''} {user.last_name or ''}".strip() or None
                )
        staff_rows = sorted(
            (
                {"id": k, "name": names.get(k), "amount": _r(v[0]), "count": v[1]}
                for k, v in staff.items()
            ),
            key=lambda x: (-x["amount"], x["name"] or ""),
        )

        total = income - refunds
        return {
            "count": len(rows),
            "total": _r(total),
            "income": _r(income),
            "income_count": income_count,
            "refunds": _r(refunds),
            "refund_count": refund_count,
            "average": _r(income / income_count) if income_count else 0.0,
            "largest": largest,
            "methods": [
                {"key": k, "amount": _r(methods[k][0]), "count": methods[k][1]}
                for k in METHOD_ORDER
                if k in methods
            ],
            "purposes": [
                {"key": k, "amount": _r(purposes[k])}
                for k in PURPOSE_ORDER
                if abs(purposes.get(k, 0.0)) > EPS
            ],
            "sources": {
                key: {"amount": _r(sources[key][0]), "count": sources[key][1]}
                for key in (purpose.SOURCE_RESERVATION, purpose.SOURCE_SHOP)
                if key in sources
            },
            "debt": {"amount": _r(debt_amount), "count": debt_count},
            "edited": edited,
            "staff": staff_rows[:TOP_STAFF],
            "staff_total": len(staff_rows),
            "days": [
                {"date": k, "amount": _r(v[0]), "count": v[1]} for k, v in sorted(days.items())
            ],
        }

    # ----------------------------------------------------- hisob-fakturalar --

    async def invoices_stats(
        self,
        hotel_id: UUID | None,
        *,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        filters: InvoiceFilters | None = None,
    ) -> dict:
        conditions = self.finance._invoice_conditions(
            hotel_id, status, date_from, date_to, search, filters
        )
        rows = (
            await self.session.execute(
                select(
                    Invoice.id,
                    Invoice.invoice_number,
                    Invoice.invoice_date,
                    Invoice.reservation_id,
                    Invoice.status,
                    Invoice.subtotal,
                    Invoice.discount_amount,
                    Invoice.tax_amount,
                    Invoice.total_amount,
                    Invoice.paid_amount,
                ).where(*conditions)
            )
        ).all()

        today = hotel_today()
        total = paid = remaining = discount = 0.0
        discount_count = 0
        debt_count = 0
        statuses: dict[str, list] = defaultdict(lambda: [0, 0.0, 0.0])
        aging: dict[str, list] = {key: [0, 0.0] for _low, _high, key in AGING_BUCKETS}
        debts: list = []
        active_total = active_paid = 0.0
        for r in rows:
            t, p = _num(r.total_amount), _num(r.paid_amount)
            left = max(t - p, 0.0)
            st = (r.status or "").upper()
            total += t
            paid += p
            if _num(r.discount_amount) > EPS:
                discount += _num(r.discount_amount)
                discount_count += 1
            s = statuses[st]
            s[0] += 1
            s[1] += t
            s[2] += left
            if st in NON_DEBT_STATUSES:
                continue
            active_total += t
            active_paid += min(p, t)
            if left > EPS:
                remaining += left
                debt_count += 1
                days_old = (today - r.invoice_date).days if r.invoice_date else 0
                for low, high, key in AGING_BUCKETS:
                    if days_old >= low and (high is None or days_old <= high):
                        aging[key][0] += 1
                        aging[key][1] += left
                        break
                debts.append((left, r))

        # Hujjat qatorlari tarkibi (xona, jarima, xizmat, ...)
        lines: dict[str, float] = defaultdict(float)
        ids = [r.id for r in rows if (r.status or "").upper() not in NON_DEBT_STATUSES]
        for chunk in _chunks(ids):
            for line_type, amount in (
                await self.session.execute(
                    select(InvoiceLineItem.line_type, func.coalesce(func.sum(InvoiceLineItem.total_price), 0))
                    .where(InvoiceLineItem.invoice_id.in_(chunk))
                    .group_by(InvoiceLineItem.line_type)
                )
            ).all():
                lines[(line_type or "OTHER").upper()] += _num(amount)

        debts.sort(key=lambda x: -x[0])
        top = debts[:TOP_DEBTS]
        people = await self.finance.invoice_people([r for _left, r in top])
        top_rows = [
            {
                "id": str(r.id),
                "invoice_number": r.invoice_number,
                "invoice_date": str(r.invoice_date) if r.invoice_date else None,
                "status": r.status,
                "total_amount": _r(r.total_amount),
                "remaining": _r(left),
                "days": (today - r.invoice_date).days if r.invoice_date else None,
                **people.get(r.reservation_id, {}),
            }
            for left, r in top
        ]

        count = len(rows)
        return {
            "count": count,
            "total": _r(total),
            "paid": _r(paid),
            "remaining": _r(remaining),
            "debt_count": debt_count,
            "discount": _r(discount),
            "discount_count": discount_count,
            "average": _r(total / count) if count else 0.0,
            # Yig'ilish foizi — bekor qilinganlarsiz hujjatlar bo'yicha
            "collection_rate": _r(active_paid / active_total * 100) if active_total > EPS else None,
            "statuses": [
                {"status": k, "count": statuses[k][0], "total": _r(statuses[k][1]), "remaining": _r(statuses[k][2])}
                for k in (*STATUS_ORDER, *sorted(set(statuses) - set(STATUS_ORDER)))
                if k in statuses
            ],
            "lines": [
                {"kind": k, "amount": _r(v)}
                for k, v in sorted(lines.items(), key=lambda kv: -kv[1])
                if abs(v) > EPS
            ],
            "aging": [
                {"bucket": key, "count": aging[key][0], "amount": _r(aging[key][1])}
                for _low, _high, key in AGING_BUCKETS
            ],
            "top_debts": top_rows,
        }


__all__ = ["FinanceStatsService", "canonical_method"]
