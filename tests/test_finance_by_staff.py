"""Moliya: tushum xodimlar (resepshnlar) kesimida — `/finance/by-staff`.

Foydalanuvchi talabi: mobil ilovaning Moliya sahifasida jami tushumni
kimlar qilgani ham ko'rinsin, ya'ni hisobot resepshnlar kesimida ham
bo'lsin. Endpoint YANGI — mavjudlariga tegilmadi. Pul yozuvning o'zidan
bog'lanadi (`created_by`) va sana ta'rifi `/finance/summary` bilan bir xil:
xodimlar bo'yicha tushum yig'indisi jami tushumga teng.
"""
import asyncio
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.application.services.finance_summary_service import (
    FinanceSummaryService,
    can_view_staff_report,
)
from app.core.exceptions import ForbiddenException, ValidationException
from app.presentation.api.v1 import finance as finance_api

HOTEL = uuid.uuid4()
DILNOZA = uuid.uuid4()
AZIZ = uuid.uuid4()
GONE = uuid.uuid4()
SALE_A, SALE_B = uuid.uuid4(), uuid.uuid4()


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class StaffSession:
    def __init__(self, payments=(), shop=(), expenses=(), users=()):
        self.payments, self.shop, self.expenses, self.users = payments, shop, expenses, users
        self.sql = []

    async def execute(self, stmt):
        sql = str(stmt)
        self.sql.append(sql)
        if "FROM payments" in sql:
            return Rows(list(self.payments))
        # Do'kon — har to'lov qatori (qisman to'lovlar ham)
        if "FROM shop_sale_payments" in sql:
            return Rows(list(self.shop))
        if "FROM expenses" in sql:
            return Rows(list(self.expenses))
        if "FROM users" in sql:
            return Rows(list(self.users))
        raise AssertionError(sql)


def build(session, **kw):
    return asyncio.run(
        FinanceSummaryService(session).by_staff(
            HOTEL, date_from=kw.get("date_from", date(2026, 10, 1)), date_to=kw.get("date_to", date(2026, 10, 7))
        )
    )


def test_revenue_is_split_by_who_took_the_money():
    session = StaffSession(
        # (created_by, usul, summa, soni, qaytarim)
        payments=[
            (DILNOZA, "CASH", Decimal("1500000"), 5, Decimal("0")),
            (DILNOZA, "CARD", Decimal("700000"), 2, Decimal("0")),
            # Qaytarim manfiy to'lov — tushumni kamaytiradi
            (AZIZ, "CASH", Decimal("250000"), 3, Decimal("50000")),
        ],
        # To'lov qatorlari: (qabul qilgan, savdo, summa, usul)
        shop=[
            (DILNOZA, SALE_A, Decimal("30000"), "CASH"),
            # Bo'lib to'langan savdo — har bo'lagi alohida qator, o'z usulida
            (AZIZ, SALE_B, Decimal("10000"), "CASH"),
            (AZIZ, SALE_B, Decimal("30000"), "CARD"),
        ],
        # (created_by, usul, summa, soni)
        expenses=[
            (AZIZ, "CASH", Decimal("120000"), 2),
            (AZIZ, "CARD", Decimal("80000"), 1),
        ],
        users=[
            (DILNOZA, "Dilnoza", "Rahimova", "EMPLOYEE", "ACTIVE"),
            (AZIZ, "Aziz", "Karimov", "ADMIN", "ACTIVE"),
        ],
    )
    out = build(session)
    first, second = out["items"]
    assert first["name"] == "Dilnoza Rahimova" and first["user_type"] == "EMPLOYEE"
    assert first["income"] == 2200000 and first["shop"] == 30000
    assert first["revenue"] == 2230000 and first["payment_count"] == 7
    assert first["cash"] == 1530000 and first["expense"] == 0
    assert {m["key"]: m for m in first["methods"]}["CARD"] == {"key": "CARD", "pay": 700000.0, "shop": 0.0}

    assert second["name"] == "Aziz Karimov"
    assert second["revenue"] == 290000 and second["refunds"] == 50000
    assert second["shop_count"] == 1 and second["cash"] == 260000
    assert second["expense"] == 200000 and second["expense_count"] == 3
    assert second["cash_expense"] == 120000
    methods = {m["key"]: m for m in second["methods"]}
    assert methods["CARD"]["shop"] == 30000 and methods["CASH"]["shop"] == 10000

    # Xodimlar yig'indisi = jami tushum (bron to'lovlari + do'kon)
    assert out["total"]["revenue"] == 2200000 + 250000 + 30000 + 40000
    assert out["total"]["expense"] == 200000 and out["total"]["payment_count"] == 10
    # Hammasi mehmonxona bo'yicha cheklangan
    assert all("hotel_id" in sql for sql in session.sql if "FROM users" not in sql)


def test_dates_match_summary_definition():
    session = StaffSession()
    build(session)
    pay, shop, exp = session.sql
    assert "payments.payment_date >=" in pay and "payments.payment_date <=" in pay
    # Do'kon — to'lov sanasi bo'yicha (qisman to'lovlar o'z kunida)
    assert "shop_sale_payments.paid_at >=" in shop and "shop_sale_payments.hotel_id" in shop
    assert "expenses.expense_date >=" in exp
    # Faoliyat bo'lmasa — bo'sh, foydalanuvchilar so'ralmaydi
    assert build(StaffSession())["items"] == []


def test_expense_only_and_unknown_user_are_kept_and_sorted():
    session = StaffSession(
        payments=[(DILNOZA, "CASH", Decimal("100"), 1, Decimal("0"))],
        expenses=[(GONE, None, Decimal("50"), 1)],
        users=[(DILNOZA, "Dilnoza", "Rahimova", "EMPLOYEE", "ACTIVE")],
    )
    out = build(session)
    assert [i["name"] for i in out["items"]] == ["Dilnoza Rahimova", None]
    gone = out["items"][1]
    assert gone["revenue"] == 0 and gone["expense"] == 50 and gone["cash_expense"] == 0


def test_access_rules():
    assert can_view_staff_report({"user_type": "ADMIN"})
    assert can_view_staff_report({"user_type": "SUPER_ADMIN"})
    for code in ("finance.view", "shift.force_close", "report.view"):
        assert can_view_staff_report({"user_type": "EMPLOYEE", "permissions": [code]})
    assert not can_view_staff_report({"user_type": "EMPLOYEE", "permissions": ["reservation.update"]})
    assert not can_view_staff_report({"user_type": "EMPLOYEE"})


def test_endpoint_guards():
    reception = {"user_type": "EMPLOYEE", "hotel_id": HOTEL, "permissions": ["reservation.create"]}
    with pytest.raises(ForbiddenException) as err:
        asyncio.run(finance_api.finance_by_staff(
            date_from=None, date_to=None, hotel_id=None, session=StaffSession(), current_user=reception,
        ))
    assert err.value.error_code == "STAFF_REPORT_FORBIDDEN"

    admin = {"user_type": "ADMIN", "hotel_id": HOTEL}
    with pytest.raises(ValidationException):
        asyncio.run(finance_api.finance_by_staff(
            date_from=date(2026, 10, 5), date_to=date(2026, 10, 1), hotel_id=None,
            session=StaffSession(), current_user=admin,
        ))
    manager = {"user_type": "EMPLOYEE", "hotel_id": HOTEL, "permissions": ["shift.force_close"]}
    out = asyncio.run(finance_api.finance_by_staff(
        date_from=date(2026, 10, 1), date_to=date(2026, 10, 1), hotel_id=None,
        session=StaffSession(payments=[(DILNOZA, "CASH", Decimal("10"), 1, Decimal("0"))],
                             users=[(DILNOZA, "Dilnoza", "R", "EMPLOYEE", "ACTIVE")]),
        current_user=manager,
    ))
    assert out["items"][0]["revenue"] == 10
