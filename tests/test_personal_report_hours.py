"""Shaxsiy hisobot: soat bilan davr — endpoint tekshiruvlari va xarajat oynasi.

Ishga tushirish: pytest tests/test_personal_report_hours.py

1. "HH:MM" dan boshqa format — 422 (FastAPI pattern).
2. Boshlanish tugashdan keyin — 422 INVALID_RANGE; tungi smena (sana
   ertasi kunga) — o'tadi.
3. Soat berilsa xarajatlar KIRITILGAN payti bo'yicha olinadi, aks holda
   avvalgidek `expense_date` bo'yicha.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, time, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.services.personal_report_service import (
    PersonalReportService,
    local_day_bounds,
)
from app.core.exceptions import ValidationException
from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.expense import Expense
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.user import User
from app.presentation.api.v1 import reports as reports_api

HOTEL = uuid.uuid4()
USER = uuid.uuid4()


def test_hhmm_parser():
    assert reports_api._parse_hhmm(None) is None
    assert reports_api._parse_hhmm("") is None
    assert reports_api._parse_hhmm("08:05") == time(8, 5)
    import re

    for bad in ("8:00", "24:00", "12:60", "12-00", "1200"):
        assert re.match(reports_api._HHMM, bad) is None
    for good in ("00:00", "08:30", "23:59"):
        assert re.match(reports_api._HHMM, good)


def _call(**kw):
    """Endpoint funksiyasini to'g'ridan-to'g'ri — sessiya ishlatilishidan oldingi tekshiruvlar."""
    params = dict(date_from=date(2026, 11, 14), date_to=date(2026, 11, 14), user_id=None,
                  time_from=None, time_to=None, session=None,
                  current_user={"id": uuid.uuid4(), "user_type": "ADMIN", "hotel_id": HOTEL})
    params.update(kw)
    return asyncio.run(reports_api.my_summary(**params))


def test_inverted_hours_are_rejected_but_overnight_passes(monkeypatch):
    with pytest.raises(ValidationException) as exc:
        _call(time_from="20:00", time_to="08:00")
    assert exc.value.error_code == "INVALID_RANGE"
    with pytest.raises(ValidationException):
        _call(time_from="10:00", time_to="10:00")

    seen = {}

    async def fake_resolve(session, current_user, hotel_id, user_id):
        return current_user["id"], None

    async def fake_summary(self, hotel_id, user_id, date_from, date_to, time_from=None, time_to=None):
        seen.update(date_from=date_from, date_to=date_to, time_from=time_from, time_to=time_to)
        return {}

    monkeypatch.setattr(reports_api, "resolve_report_user", fake_resolve)
    monkeypatch.setattr(PersonalReportService, "summary", fake_summary)
    # Tungi smena: 14-kun 20:00 → 15-kun 08:00
    out = _call(date_to=date(2026, 11, 15), time_from="20:00", time_to="08:00")
    assert seen == {"date_from": date(2026, 11, 14), "date_to": date(2026, 11, 15),
                    "time_from": time(20, 0), "time_to": time(8, 0)}
    assert out["is_self"] is True
    # Soatsiz teskari sanalar avvalgidek almashtiriladi
    _call(date_from=date(2026, 11, 15), date_to=date(2026, 11, 14))
    assert seen["date_from"] == date(2026, 11, 14) and seen["time_from"] is None


def test_expenses_use_entry_time_when_hours_given():
    async def main():
        engine = create_async_engine("sqlite+aiosqlite://")
        tables = [Hotel.__table__, Branch.__table__, User.__table__, Expense.__table__]
        async with engine.begin() as conn:
            await conn.run_sync(lambda s: Base.metadata.create_all(s, tables=tables))
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with maker() as session:
                def exp(title, created_utc, expense_date=date(2026, 11, 14)):
                    return Expense(id=uuid.uuid4(), hotel_id=HOTEL, title=title, amount=1000, payment_method="CASH",
                                   expense_date=expense_date, created_by=USER, created_at=created_utc)
                session.add_all([
                    exp("ertalab", datetime(2026, 11, 14, 4, 0, tzinfo=timezone.utc)),   # 09:00 mahalliy
                    exp("kechqurun", datetime(2026, 11, 14, 16, 0, tzinfo=timezone.utc)),  # 21:00 mahalliy
                    # Sanasi kechagi qilib kiritilgan, lekin bugun 10:00 da yozilgan
                    exp("kechagisi", datetime(2026, 11, 14, 5, 0, tzinfo=timezone.utc), date(2026, 11, 13)),
                ])
                await session.flush()
                svc = PersonalReportService(session)
                day = await svc._expenses(None, USER, date(2026, 11, 14), date(2026, 11, 14))
                assert sorted(i["title"] for i in day["items"]) == ["ertalab", "kechqurun"]
                window = local_day_bounds(date(2026, 11, 14), date(2026, 11, 14), time(8, 0), time(20, 0))
                shift = await svc._expenses(None, USER, date(2026, 11, 14), date(2026, 11, 14), window=window)
                assert sorted(i["title"] for i in shift["items"]) == ["ertalab", "kechagisi"]
                assert shift["total"] == 2000.0
        finally:
            await engine.dispose()

    asyncio.run(main())
