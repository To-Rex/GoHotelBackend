"""Xarajat yozish: qisman (aralash) to'lov va o'chirish (expense_service).

Ishga tushirish: pytest tests/test_expense_service.py

Nozik joylar:

1. Qisman to'lovda har bo'lak ALOHIDA qator (o'z summasi, o'z to'lov turi),
   hammasi bitta `split_group` bilan. Hisobotlar qatorlar bo'yicha
   ishlayveradi — kassadan faqat naqd bo'lak chiqadi.

2. Bo'laklar yig'indisi summaga teng bo'lishi shart; bir to'lov turi bir
   marta; 0 bo'laklar tashlanadi; bittagina bo'lak qolsa — oddiy xarajat.

3. Bo'lak o'chirilsa BUTUN xarajat o'chadi — yarim xarajat qolmaydi.

4. Moliya yig'indisi va shaxsiy hisobot xarajat SONIni guruh bo'yicha
   sanaydi: 60 000 naqd + 40 000 online — bitta xarajat, summasi 100 000.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.services.expense_category_service import ExpenseCategoryService
from app.application.services.expense_service import (
    MAX_PARTS,
    ExpenseService,
    expense_dict,
    normalize_parts,
)
from app.application.services.finance_summary_service import FinanceSummaryService
from app.application.services.personal_report_service import PersonalReportService
from app.core.exceptions import NotFoundException, ValidationException
from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.expense import Expense
from app.infrastructure.database.models.expense_category import ExpenseCategory
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.user import User

HOTEL = uuid.uuid4()
OTHER_HOTEL = uuid.uuid4()
USER_ID = uuid.uuid4()
STAFF = {"user_type": "EMPLOYEE", "id": USER_ID, "permissions": ["expense.create"], "hotel_id": HOTEL}
TODAY = date(2026, 11, 8)


def run(body):
    async def main():
        engine = create_async_engine("sqlite+aiosqlite://")
        tables = [Hotel.__table__, Branch.__table__, User.__table__, Expense.__table__, ExpenseCategory.__table__]
        async with engine.begin() as conn:
            await conn.run_sync(lambda s: Base.metadata.create_all(s, tables=tables))
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with maker() as session:
                await body(session)
        finally:
            await engine.dispose()

    asyncio.run(main())


def base(**over) -> dict:
    data = {
        "title": "Kommunal, oktyabr", "amount": 100000.0, "category": "gaz",
        "payment_method": "CASH", "payments": None, "expense_date": TODAY, "notes": None, "branch_id": None,
    }
    data.update(over)
    return data


# -------------------------------------------------------- bo'laklar --


def test_parts_are_cleaned_and_validated():
    assert normalize_parts(100000, None) == []
    assert normalize_parts(100000, []) == []
    # 0 va bo'sh bo'laklar tashlanadi, tartib saqlanadi
    assert normalize_parts(
        100000,
        [{"payment_method": "CASH", "amount": 60000}, {"payment_method": "CARD", "amount": 0},
         {"payment_method": "online", "amount": "40000"}],
    ) == [("CASH", 60000.0), ("ONLINE", 40000.0)]
    # Bitta bo'lak — qaytadi (chaqiruvchi oddiy xarajat qiladi)
    assert normalize_parts(100000, [{"payment_method": "CARD", "amount": 100000}]) == [("CARD", 100000.0)]
    # Tiyin farqi xato emas
    assert normalize_parts(100, [{"payment_method": "CASH", "amount": 33.333}, {"payment_method": "CARD", "amount": 66.667}]) == [("CASH", 33.33), ("CARD", 66.67)]

    with pytest.raises(ValidationException) as e:
        normalize_parts(100000, [{"payment_method": "CASH", "amount": 60000}, {"payment_method": "CARD", "amount": 30000}])
    assert e.value.error_code == "PARTS_SUM_MISMATCH"
    with pytest.raises(ValidationException) as e:
        normalize_parts(100000, [{"payment_method": "CASH", "amount": 60000}, {"payment_method": "cash", "amount": 40000}])
    assert e.value.error_code == "PARTS_DUPLICATE_METHOD"
    with pytest.raises(ValidationException) as e:
        normalize_parts(100000, [{"payment_method": "", "amount": 100000}])
    assert e.value.error_code == "PART_METHOD_REQUIRED"
    with pytest.raises(ValidationException) as e:
        normalize_parts(5, [{"payment_method": f"M{i}", "amount": 1} for i in range(MAX_PARTS + 1)])
    assert e.value.error_code == "PARTS_TOO_MANY"


# ------------------------------------------------------------ yozish --


def test_single_expense_is_one_row_with_resolved_category():
    async def body(session):
        service = ExpenseService(session)
        # Toifalar ro'yxati — standart, "gaz" → "Gaz"
        await ExpenseCategoryService(session).list_categories(HOTEL)

        out = await service.create(HOTEL, base(), STAFF)
        assert out["amount"] == 100000.0 and out["payment_method"] == "CASH"
        assert out["category"] == "Gaz" and out["split_group"] is None and "parts" not in out
        rows = (await session.execute(select(Expense))).scalars().all()
        assert len(rows) == 1 and rows[0].split_group is None

        # Bitta bo'lak = oddiy xarajat o'sha to'lov turi bilan
        out = await service.create(HOTEL, base(payments=[{"payment_method": "ONLINE", "amount": 100000}]), STAFF)
        assert out["payment_method"] == "ONLINE" and out["split_group"] is None

        with pytest.raises(ValidationException):
            await service.create(HOTEL, base(amount=0), STAFF)

    run(body)


def test_split_expense_writes_one_row_per_part():
    async def body(session):
        service = ExpenseService(session)
        out = await service.create(
            HOTEL,
            base(payments=[{"payment_method": "CASH", "amount": 60000}, {"payment_method": "ONLINE", "amount": 40000}]),
            STAFF,
        )
        assert out["split_group"] and out["amount"] == 60000.0 and out["payment_method"] == "CASH"
        assert [(p["payment_method"], p["amount"]) for p in out["parts"]] == [("CASH", 60000.0), ("ONLINE", 40000.0)]
        assert {p["split_group"] for p in out["parts"]} == {out["split_group"]}

        rows = (await session.execute(select(Expense).order_by(Expense.amount.desc()))).scalars().all()
        assert len(rows) == 2
        assert all(str(r.split_group) == out["split_group"] for r in rows)
        # Umumiy maydonlar bir xil
        assert {r.title for r in rows} == {"Kommunal, oktyabr"}
        assert {r.expense_date for r in rows} == {TODAY}
        assert {r.created_by for r in rows} == {USER_ID}
        assert sum(float(r.amount) for r in rows) == 100000.0

        with pytest.raises(ValidationException):
            await service.create(
                HOTEL,
                base(payments=[{"payment_method": "CASH", "amount": 60000}, {"payment_method": "ONLINE", "amount": 50000}]),
                STAFF,
            )
        # Xato bo'lsa hech narsa yozilmaydi
        assert len((await session.execute(select(Expense))).scalars().all()) == 2

    run(body)


def test_delete_removes_the_whole_split_group_only():
    async def body(session):
        service = ExpenseService(session)
        split = await service.create(
            HOTEL,
            base(payments=[{"payment_method": "CASH", "amount": 60000}, {"payment_method": "ONLINE", "amount": 40000}]),
            STAFF,
        )
        single = await service.create(HOTEL, base(amount=5000), STAFF)
        other = await service.create(
            HOTEL,
            base(payments=[{"payment_method": "CASH", "amount": 1}, {"payment_method": "CARD", "amount": 1}], amount=2),
            STAFF,
        )
        with pytest.raises(NotFoundException):
            await service.delete(OTHER_HOTEL, uuid.UUID(split["id"]))
        with pytest.raises(NotFoundException):
            await service.delete(HOTEL, uuid.uuid4())

        # Ikkinchi bo'lak orqali o'chirilsa ham butun guruh ketadi
        second = uuid.UUID(split["parts"][1]["id"])
        assert await service.delete(HOTEL, second) == 2
        left = (await session.execute(select(Expense))).scalars().all()
        assert {str(r.id) for r in left} == {single["id"], *(p["id"] for p in other["parts"])}

        assert await service.delete(HOTEL, uuid.UUID(single["id"])) == 1
        assert await service.delete(None, uuid.UUID(other["id"])) == 2
        assert (await session.execute(select(Expense))).scalars().all() == []

    run(body)


def test_expense_dict_carries_split_group():
    row = Expense(
        id=uuid.uuid4(), hotel_id=HOTEL, title="t", category=None, amount=10, payment_method="CASH",
        expense_date=TODAY, created_by=USER_ID, split_group=None,
    )
    assert expense_dict(row, None)["split_group"] is None
    row.split_group = uuid.uuid4()
    assert expense_dict(row, None)["split_group"] == str(row.split_group)


# --------------------------------------------------------- hisobotlar --


def test_reports_count_a_split_expense_once():
    async def body(session):
        service = ExpenseService(session)
        # Standart toifalar — "gaz" → "Gaz"
        await ExpenseCategoryService(session).list_categories(HOTEL)
        await service.create(
            HOTEL,
            base(payments=[{"payment_method": "CASH", "amount": 60000}, {"payment_method": "ONLINE", "amount": 40000}]),
            STAFF,
        )
        await service.create(HOTEL, base(amount=25000, category="Suv", payment_method="CARD"), STAFF)

        bucket_store: dict = {}

        def bucket(method):
            return bucket_store.setdefault(method, {"expense": 0.0})

        summary = await FinanceSummaryService(session)._expenses(HOTEL, TODAY, TODAY, bucket)
        assert summary["expense_total"] == 125000.0
        assert summary["expense_count"] == 2  # 3 qator, 2 xarajat
        cats = {c["name"]: c for c in summary["expense_categories"]}
        assert cats["Gaz"]["count"] == 1 and cats["Gaz"]["total"] == 100000.0
        assert cats["Suv"]["count"] == 1
        # Usullar kesimi qatorlar bo'yicha — har bo'lak o'z usulida
        assert bucket_store["CASH"]["expense"] == 60000.0
        assert bucket_store["ONLINE"]["expense"] == 40000.0
        assert bucket_store["CARD"]["expense"] == 25000.0

        personal = await PersonalReportService(session)._expenses(HOTEL, USER_ID, TODAY, TODAY)
        assert personal["count"] == 2 and personal["total"] == 125000.0
        assert len(personal["items"]) == 3
        groups = {i["split_group"] for i in personal["items"] if i["split_group"]}
        assert len(groups) == 1

    run(body)
