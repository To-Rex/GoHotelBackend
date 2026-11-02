"""Jarima uchun davrda OLINGAN pul (`penalty_paid`) — moliya yig'masi uchun.

To'lov hisob-fakturaga yoziladi, qatorlarga emas; jarima OXIRIDA yopiladi
degan qoida bilan ajratiladi:

    yopilgan(t) = clamp(t gacha to'langan − (jami − jarima), 0, jarima)
    davrda olingan = yopilgan(davr oxiri) − yopilgan(davr boshigacha)

Bu pul `income` ichida allaqachon bor — sahifa uni ajratib ko'rsatadi,
qo'shmaydi. Bazasiz: sessiya o'rinbosari SQL matniga qarab javob beradi.
"""
import asyncio
import uuid
from datetime import date

from app.application.services.reservation_penalty_service import penalty_paid, penalty_totals

HOTEL = uuid.uuid4()
INV_A, INV_B = uuid.uuid4(), uuid.uuid4()
D1, D2 = date(2026, 11, 1), date(2026, 11, 30)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def one(self):
        return self.rows[0]


class Db:
    def __init__(self, invoices=(), payments=(), penalties=(0, 0)):
        self.invoices, self.payments, self.penalties = list(invoices), list(payments), penalties
        self.sql: list[str] = []

    async def execute(self, stmt):
        sql = str(stmt)
        self.sql.append(sql)
        if "FROM reservation_penalties" in sql:
            return Rows([self.penalties])
        if "FROM invoices" in sql:
            return Rows(self.invoices)
        if "FROM payments" in sql:
            return Rows(self.payments)
        raise AssertionError(sql)


def test_no_penalty_invoices_means_zero_and_no_payment_query():
    db = Db()
    assert asyncio.run(penalty_paid(db, HOTEL, D1, D2)) == 0.0
    assert not any("FROM payments" in s for s in db.sql)
    # Mehmonxona bo'yicha cheklangan, faqat PENALTY qatorlari
    assert "hotel_id" in db.sql[0] and "line_type" in db.sql[0]


def test_penalty_is_covered_last():
    # Jami 650 000, shundan jarima 150 000 (asosiy 500 000).
    # Davr boshigacha 500 000 to'langan (asosiy yopilgan), davr ichida yana
    # 150 000 — bu aynan jarima puli.
    db = Db(invoices=[(INV_A, 650000, 150000)], payments=[(INV_A, 500000, 650000)])
    assert asyncio.run(penalty_paid(db, HOTEL, D1, D2)) == 150000.0


def test_partial_payment_counts_only_the_penalty_part():
    # Davr boshigacha 400 000 (asosiyning o'zi ham to'liq emas), davr oxirida
    # 600 000 — jarimadan 100 000 yopildi, qolgan 50 000 hali qarz
    db = Db(invoices=[(INV_A, 650000, 150000)], payments=[(INV_A, 400000, 600000)])
    assert asyncio.run(penalty_paid(db, HOTEL, D1, D2)) == 100000.0


def test_penalty_paid_before_the_period_is_not_counted_again():
    db = Db(invoices=[(INV_A, 650000, 150000)], payments=[(INV_A, 650000, 650000)])
    assert asyncio.run(penalty_paid(db, HOTEL, D1, D2)) == 0.0


def test_refund_in_period_reduces_received_penalty():
    # Davr boshida jarima to'liq yopilgan edi (650 000), davrda 100 000 qaytarildi —
    # jarimaning 100 000 qismi "qaytib ketdi": manfiy ham bo'lmaydi, yig'ma 0 ga tushadi
    db = Db(invoices=[(INV_A, 650000, 150000)], payments=[(INV_A, 650000, 550000)])
    assert asyncio.run(penalty_paid(db, HOTEL, D1, D2)) == 0.0


def test_several_invoices_are_summed_and_overpayment_is_capped():
    db = Db(
        invoices=[(INV_A, 650000, 150000), (INV_B, 300000, 50000)],
        # A: jarima to'liq (150 000); B: ortiqcha to'langan — jarimadan oshmaydi (50 000)
        payments=[(INV_A, 500000, 650000), (INV_B, 0, 400000)],
    )
    assert asyncio.run(penalty_paid(db, HOTEL, D1, D2)) == 200000.0


def test_open_range_uses_all_payments():
    # date_from yo'q — "davr boshigacha" 0 (literal), date_to yo'q — barcha to'lovlar
    db = Db(invoices=[(INV_A, 650000, 150000)], payments=[(INV_A, 0, 650000)])
    assert asyncio.run(penalty_paid(db, HOTEL, None, None)) == 150000.0
    assert "payment_date" not in db.sql[-1]


def test_penalty_totals_includes_paid_and_keeps_old_fields():
    db = Db(
        penalties=(300000, 2),
        invoices=[(INV_A, 650000, 150000)],
        payments=[(INV_A, 500000, 650000)],
    )
    out = asyncio.run(penalty_totals(db, HOTEL, D1, D2))
    assert out == {"penalty_total": 300000.0, "penalty_count": 2, "penalty_paid": 150000.0}
