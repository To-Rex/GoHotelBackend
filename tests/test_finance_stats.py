"""Moliya → To'lovlar va Hisob-fakturalar statistikasi (finance_stats_service).

Ishga tushirish: pytest tests/test_finance_stats.py

Nozik joylar:

1. Statistika ro'yxat bilan BIR XIL shartlardan: "Turi", to'lov turi,
   xodim, summa, tahrirlangan, qidiruv — jadvalda nima qolsa, statistika
   ham shundan.
2. "Nima uchun" — jadvaldagi belgilar qoidasi (FIFO taqsimot): xona,
   jarima, ortiqcha; qarz to'lovi (chiqib ketgandan keyin) alohida;
   qaytarim manfiy.
3. Eski to'lov kodi o'z turiga (CREDIT_CARD → karta).
4. Hisob-fakturalar: bekor qilingan / qaytarilgan hujjat qarz emas va
   yig'ilish foiziga kirmaydi; qarz yoshi hujjat sanasidan.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select, text

from app.application.services import finance_stats_service as stats_mod
from app.application.services.finance_service import (
    InvoiceFilters,
    PaymentFeedFilters,
    method_codes,
)
from app.application.services.finance_stats_service import (
    FinanceStatsService,
    canonical_method,
)
from app.infrastructure.database.models.invoice import Invoice, InvoiceLineItem
from app.infrastructure.database.models.payment import Payment
from tests.test_finance_filters import BRANCH, HOTEL, at, run

TODAY = date(2026, 11, 22)


def k(*values) -> frozenset:
    return frozenset(values)


async def pstats(session, **filters) -> dict:
    return await FinanceStatsService(session).payments_stats(HOTEL, filters=PaymentFeedFilters(**filters))


def test_canonical_methods():
    assert canonical_method("CREDIT_CARD") == "CARD"
    assert canonical_method("mobile_payment") == "ONLINE"
    assert canonical_method("TRANSFER") == "BANK_TRANSFER"
    assert canonical_method("MIXED") == "MIXED"
    assert canonical_method(None) == "OTHER" and canonical_method("BITCOIN") == "OTHER"


def test_payment_stats_without_filters():
    async def body(session, ids):
        s = await pstats(session)
        assert s["count"] == 4 and s["income"] == 470_000.0 and s["income_count"] == 4
        assert s["refunds"] == 0.0 and s["total"] == 470_000.0
        assert s["average"] == 117_500.0
        assert s["largest"]["amount"] == 300_000.0 and s["largest"]["number"] == "PAY-1"
        # Eski karta kodi kartaga; tartib — naqd, karta, online
        assert [(m["key"], m["amount"], m["count"]) for m in s["methods"]] == [
            ("CASH", 320_000.0, 2), ("CARD", 50_000.0, 1), ("ONLINE", 100_000.0, 1),
        ]
        # Nima uchun: xona 300 000, jarima 50 000, ortiqcha 120 000
        assert [(p["key"], p["amount"]) for p in s["purposes"]] == [
            ("ROOM", 300_000.0), ("PENALTY", 50_000.0), ("EXTRA", 120_000.0),
        ]
        assert s["debt"] == {"amount": 120_000.0, "count": 2}
        assert s["edited"] == 1
        assert s["sources"] == {"RESERVATION": {"amount": 470_000.0, "count": 4}}
        assert [(x["name"], x["amount"], x["count"]) for x in s["staff"]] == [
            ("Aziz Karimov", 400_000.0, 2), ("Bobur Aliyev", 70_000.0, 2),
        ]
        assert s["staff_total"] == 2
        assert [(d["date"], d["amount"], d["count"]) for d in s["days"]] == [
            ("2026-11-01", 300_000.0, 1), ("2026-11-02", 50_000.0, 1), ("2026-11-04", 120_000.0, 2),
        ]

    run(body)


def test_payment_stats_follow_the_list_filters():
    async def body(session, ids):
        s = await pstats(session, kinds=k("PENALTY"))
        assert s["count"] == 1 and s["total"] == 50_000.0
        assert [p["key"] for p in s["purposes"]] == ["PENALTY"]
        s = await pstats(session, kinds=k("DEBT"))
        assert s["count"] == 2 and s["debt"]["amount"] == 120_000.0
        assert [p["key"] for p in s["purposes"]] == ["EXTRA"]
        s = await pstats(session, methods=method_codes("CARD"))
        assert s["count"] == 1 and s["methods"][0]["key"] == "CARD"
        s = await pstats(session, created_by=ids["bobur"])
        assert s["total"] == 70_000.0 and len(s["staff"]) == 1
        s = await pstats(session, edited=True)
        assert s["count"] == 1 and s["edited"] == 1
        s = await pstats(session, min_amount=100_000)
        assert s["count"] == 2
        # Davr va qidiruv ham
        s = await FinanceStatsService(session).payments_stats(
            HOTEL, date_from=date(2026, 11, 2), date_to=date(2026, 11, 2)
        )
        assert s["count"] == 1 and s["total"] == 50_000.0
        s = await FinanceStatsService(session).payments_stats(HOTEL, search="PAY-4")
        assert s["count"] == 1 and s["total"] == 20_000.0
        # Hech narsa topilmasa — nollar
        s = await pstats(session, kinds=k("SERVICE"))
        assert s["count"] == 0 and s["largest"] is None and s["average"] == 0.0 and s["methods"] == []

    run(body)


def test_refunds_are_subtracted():
    async def body(session, ids):
        invoice = (await session.execute(select(Invoice))).scalar_one()
        # Qaytarim — manfiy to'lov qatori (bron bekor qilinganda server shunday
        # yozadi); modeldagi `amount > 0` CHECK i faqat SQLite jadvalida bor
        await session.execute(text("PRAGMA ignore_check_constraints = 1"))
        session.add(Payment(
            hotel_id=HOTEL, branch_id=BRANCH, invoice_id=invoice.id, payment_number="PAY-5", amount=-30_000,
            payment_method="CASH", payment_date=date(2026, 11, 5), created_by=ids["aziz"], created_at=at(5, 10),
        ))
        await session.commit()
        s = await pstats(session)
        assert s["income"] == 470_000.0 and s["refunds"] == 30_000.0 and s["refund_count"] == 1
        assert s["total"] == 440_000.0 and s["average"] == 117_500.0
        assert {m["key"]: m["amount"] for m in s["methods"]}["CASH"] == 290_000.0
        assert {p["key"]: p["amount"] for p in s["purposes"]}["REFUND"] == -30_000.0
        s = await pstats(session, kinds=k("REFUND"))
        assert s["count"] == 1 and s["total"] == -30_000.0 and s["income_count"] == 0

    run(body)


def test_invoice_stats(monkeypatch):
    monkeypatch.setattr(stats_mod, "hotel_today", lambda: TODAY)

    async def body(session, ids):
        first = (await session.execute(select(Invoice))).scalar_one()

        def inv(number, day, total, paid, status, discount=0):
            return Invoice(
                hotel_id=HOTEL, branch_id=BRANCH, reservation_id=first.reservation_id, guest_id=first.guest_id,
                invoice_number=number, invoice_date=day, total_amount=total, paid_amount=paid,
                discount_amount=discount, status=status, created_by=ids["aziz"],
            )

        second = inv("INV-2", date(2026, 11, 1), 200_000, 50_000, "ISSUED")
        void = inv("INV-3", date(2026, 11, 10), 100_000, 0, "VOID")
        old = inv("INV-4", date(2026, 8, 1), 80_000, 30_000, "PARTIALLY_PAID", discount=20_000)
        session.add_all([second, void, old])
        await session.flush()
        session.add(InvoiceLineItem(invoice_id=second.id, hotel_id=HOTEL, branch_id=BRANCH, description="Nonushta",
                                    line_type="SERVICE", unit_price=200_000, total_price=200_000,
                                    created_at=at(1, 15)))
        session.add(InvoiceLineItem(invoice_id=void.id, hotel_id=HOTEL, branch_id=BRANCH, description="Bekor",
                                    line_type="ROOM_CHARGE", unit_price=100_000, total_price=100_000,
                                    created_at=at(10, 15)))
        await session.commit()

        service = FinanceStatsService(session)
        s = await service.invoices_stats(HOTEL)
        assert s["count"] == 4 and s["total"] == 730_000.0 and s["paid"] == 430_000.0
        # Qarz — faqat faol hujjatlar: 150 000 + 50 000
        assert s["remaining"] == 200_000.0 and s["debt_count"] == 2
        assert s["discount"] == 20_000.0 and s["discount_count"] == 1
        assert s["average"] == 182_500.0
        assert s["collection_rate"] == round(430_000 / 630_000 * 100, 2)
        assert [x["status"] for x in s["statuses"]] == ["ISSUED", "PARTIALLY_PAID", "PAID", "VOID"]
        assert {x["status"]: x["remaining"] for x in s["statuses"]}["VOID"] == 100_000.0
        # Qatorlar: bekor qilingan hujjat qatorlari kirmaydi
        assert {x["kind"]: x["amount"] for x in s["lines"]} == {
            "ROOM_CHARGE": 300_000.0, "SERVICE": 200_000.0, "PENALTY": 50_000.0,
        }
        assert {a["bucket"]: (a["count"], a["amount"]) for a in s["aging"]} == {
            "0_7": (0, 0.0), "8_30": (1, 150_000.0), "31_90": (0, 0.0), "90_plus": (1, 50_000.0),
        }
        assert [(d["invoice_number"], d["remaining"], d["days"]) for d in s["top_debts"]] == [
            ("INV-2", 150_000.0, 21), ("INV-4", 50_000.0, 113),
        ]
        assert s["top_debts"][0]["guest_name"] == "Ali Valiyev" and s["top_debts"][0]["room_number"] == "12"

        # Ro'yxat filtrlari bilan bir xil
        assert (await service.invoices_stats(HOTEL, status="PAID"))["count"] == 1
        debt = await service.invoices_stats(HOTEL, filters=InvoiceFilters(debt_only=True))
        assert debt["count"] == 2 and debt["remaining"] == 200_000.0
        assert (await service.invoices_stats(HOTEL, filters=InvoiceFilters(discount_only=True)))["count"] == 1
        assert (await service.invoices_stats(HOTEL, search="INV-4"))["count"] == 1
        period = await service.invoices_stats(HOTEL, date_from=date(2026, 11, 1), date_to=date(2026, 11, 30))
        assert period["count"] == 3
        # Faqat bekor qilingan — yig'ilish foizi yo'q
        assert (await service.invoices_stats(HOTEL, status="VOID"))["collection_rate"] is None
        empty = await service.invoices_stats(uuid.uuid4())
        assert empty["count"] == 0 and empty["average"] == 0.0 and empty["top_debts"] == []

    run(body)


def test_routes_are_registered_before_the_id_route():
    from app.presentation.api.v1.finance import router

    paths = [r.path for r in router.routes if "GET" in getattr(r, "methods", set())]
    assert paths.index("/invoices/stats") < paths.index("/invoices/{invoice_id}")
    assert "/payments/stats" in paths
