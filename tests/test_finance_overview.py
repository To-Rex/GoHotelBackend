"""Mobil "Moliya" sahifasi uchun: kunlik qatorlar va kassalar holati.

Foydalanuvchi talabi: admin/menejer moliya bo'limida bugun, kecha, 7 kun,
oy va istalgan davr hisobotini, kassada qancha pul borligini va shunga
o'xshash ma'lumotlarni ko'rsin. Ikkala endpoint ham YANGI — mavjudlariga
tegilmadi; raqamlar `/finance/summary` va smena topshirish hisobi bilan
bir xil formulada.
"""
import asyncio
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.application.services.finance_summary_service import FinanceSummaryService
from app.application.services.shift_service import ShiftService
from app.core.exceptions import ForbiddenException, ValidationException
from app.presentation.api.v1 import finance as finance_api

HOTEL = uuid.uuid4()


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class DailySession:
    """Uchta guruhlash so'rovi: to'lovlar, xarajatlar, do'kon."""

    def __init__(self, payments, expenses, shop):
        self.payments, self.expenses, self.shop = payments, expenses, shop
        self.sql = []

    async def execute(self, stmt):
        sql = str(stmt)
        self.sql.append(sql)
        if "FROM payments" in sql:
            return Rows(self.payments)
        if "FROM expenses" in sql:
            return Rows(self.expenses)
        # Do'kon tushumi — har to'lov (qisman ham) o'z kunida
        if "FROM shop_sale_payments" in sql:
            return Rows(self.shop)
        raise AssertionError(sql)


def test_daily_series_is_zero_filled_and_grouped():
    d1, d2, d3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)
    session = DailySession(
        payments=[(d1, Decimal("500000"), 3, Decimal("0")), (d3, Decimal("-50000"), 1, Decimal("50000"))],
        expenses=[(d2, Decimal("120000"))],
        # func.date() natijasi date yoki datetime bo'lishi mumkin
        shop=[(datetime(2026, 10, 1, tzinfo=timezone.utc), Decimal("30000")), (d3, Decimal("10000"))],
    )
    days = asyncio.run(FinanceSummaryService(session).daily(HOTEL, d1, d3))
    assert [d["date"] for d in days] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert days[0] == {
        "date": "2026-10-01", "income": 500000.0, "payment_count": 3,
        "refunds": 0.0, "expense": 0.0, "shop": 30000.0,
    }
    assert days[1]["expense"] == 120000.0 and days[1]["income"] == 0.0
    assert days[2]["income"] == -50000.0 and days[2]["refunds"] == 50000.0
    assert days[2]["shop"] == 10000.0
    # Mehmonxona bo'yicha cheklangan
    assert all("hotel_id" in sql for sql in session.sql)


def test_daily_single_day():
    session = DailySession([], [], [])
    days = asyncio.run(FinanceSummaryService(session).daily(HOTEL, date(2026, 10, 2), date(2026, 10, 2)))
    assert len(days) == 1 and days[0]["income"] == 0.0


def test_daily_endpoint_validates_the_range():
    user = {"user_type": "ADMIN", "hotel_id": HOTEL}
    with pytest.raises(ValidationException) as err:
        asyncio.run(finance_api.finance_daily(
            date_from=date(2026, 10, 5), date_to=date(2026, 10, 1), hotel_id=None,
            session=DailySession([], [], []), current_user=user,
        ))
    assert err.value.error_code == "INVALID_RANGE"
    with pytest.raises(ValidationException) as err:
        asyncio.run(finance_api.finance_daily(
            date_from=date(2025, 1, 1), date_to=date(2026, 10, 1), hotel_id=None,
            session=DailySession([], [], []), current_user=user,
        ))
    assert err.value.error_code == "RANGE_TOO_LONG"
    days = asyncio.run(finance_api.finance_daily(
        date_from=date(2026, 9, 1), date_to=date(2026, 9, 30), hotel_id=None,
        session=DailySession([], [], []), current_user=user,
    ))
    assert len(days) == 30


# ------------------------------------------------------------- kassalar --
def make_service(mode="cash", sessions=()):
    svc = ShiftService.__new__(ShiftService)

    class Db:
        async def execute(self, stmt):
            return SimpleNamespace(scalars=lambda: [SimpleNamespace(id=b, name=f"Filial {i}") for i, b in enumerate(BRANCHES)])

    svc.session = Db()

    async def settings(hotel_id):
        return {"mode": mode, "day_close": "00:00", "day_close_required": True}

    async def open_sessions(hotel_id, branch_id):
        assert branch_id is None  # butun mehmonxona
        return list(sessions)

    async def breakdown(s):
        opening = Decimal(str(s.opening_cash))
        return {
            "opening_cash": opening,
            "payments_cash": Decimal("300000"),
            "shop_cash": Decimal("20000"),
            "expenses_cash": Decimal("50000"),
            "expected_cash": opening + Decimal("270000"),
        }

    svc.get_settings = settings
    svc._open_sessions = open_sessions
    svc.cash_breakdown = breakdown
    return svc


BRANCHES = [uuid.uuid4(), uuid.uuid4()]


def shift(status, opening, branch, counted=None):
    s = SimpleNamespace(
        id=uuid.uuid4(), user_id=uuid.uuid4(), status=status, branch_id=branch,
        started_at=datetime(2026, 10, 2, 8, tzinfo=timezone.utc), ended_at=None,
        opening_cash=opening, continue_after_end=False, force_closed=False, notes=None,
        counted_cash=counted, expected_cash=None, cash_diff=None, corrections=None,
    )
    u = SimpleNamespace(first_name="Dilnoza", last_name="R")
    return s, u


ADMIN = {"id": str(uuid.uuid4()), "user_type": "ADMIN", "permissions": []}
MANAGER = {"id": str(uuid.uuid4()), "user_type": "EMPLOYEE", "permissions": ["shift.force_close"]}
RECEPTION = {"id": str(uuid.uuid4()), "user_type": "EMPLOYEE", "permissions": ["reservation.create"]}


def test_cash_overview_only_for_admin_and_manager():
    with pytest.raises(ForbiddenException):
        asyncio.run(make_service().cash_overview(HOTEL, RECEPTION))
    asyncio.run(make_service().cash_overview(HOTEL, MANAGER))
    asyncio.run(make_service().cash_overview(HOTEL, ADMIN))


def test_cash_overview_simple_mode_is_empty():
    result = asyncio.run(make_service("simple", [shift("ACTIVE", 0, BRANCHES[0])]).cash_overview(HOTEL, ADMIN))
    assert result == {
        "mode": "simple", "sessions": [], "total_expected": 0.0,
        "active_count": 0, "pending_count": 0,
    }


def test_cash_overview_sums_open_drawers():
    sessions = [
        shift("ACTIVE", 100000, BRANCHES[0]),
        shift("PENDING_HANDOVER", 50000, BRANCHES[1], counted=Decimal("315000")),
    ]
    result = asyncio.run(make_service("cash", sessions).cash_overview(HOTEL, ADMIN))
    assert result["mode"] == "cash"
    assert result["active_count"] == 1 and result["pending_count"] == 1
    assert result["total_expected"] == 370000.0 + 320000.0
    first, second = result["sessions"]
    assert first["expected_cash"] == 370000.0 and first["payments_cash"] == 300000.0
    assert first["counted_cash"] is None
    assert first["branch_name"] == "Filial 0" and first["user_name"] == "Dilnoza R"
    assert second["counted_cash"] == 315000.0 and second["status"] == "PENDING_HANDOVER"
