"""Xarajatni tahrirlash (expense_service.ExpenseService.update).

Ishga tushirish: pytest tests/test_expense_edit.py

Nozik joylar:

1. Huquq: faqat administrator, super admin va mehmonxona tanlagan sozlovchi
   (so'rovda u "ADMIN" bo'lib keladi). Xarajatni o'chira oladigan xodim
   (`expense.delete`) ham tahrirlay OLMAYDI.

2. Xarajat QACHON va KIM tomonidan kiritilgani o'zgarmaydi: smena kassasi
   va shaxsiy hisobot xarajatni `created_by` + `created_at` bo'yicha
   topadi — tahrirdan keyin ham o'sha smenada qoladi. Yangi bo'lak ham
   asl xarajatning vaqti va xodimini oladi.

3. Bo'lingan xarajat butunligicha tahrirlanadi: istalgan bo'lak id si
   bilan; summa o'zgarsa bo'laklar shart; o'sha to'lov turi — o'sha qator.

4. Har tahrir `edit_history` da (kim, qachon, vazifa, nimadan nimaga,
   izoh) va audit jurnalida; audit xatosi tahrirni buzmaydi.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.application.services.expense_category_service import ExpenseCategoryService
from app.application.services.expense_service import (
    EDIT_FORBIDDEN_CODE,
    MAX_HISTORY,
    ExpenseService,
    can_edit_expenses,
    diff_snapshots,
    expense_dict,
)
from app.application.services.finance_summary_service import FinanceSummaryService
from app.core.exceptions import (
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.infrastructure.database.models.audit_log import AuditLog
from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.expense import Expense
from app.infrastructure.database.models.expense_category import ExpenseCategory
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.user import User


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(_type, _compiler, **_kw):  # audit_logs jadvali uchun
    return "JSON"


HOTEL = uuid.uuid4()
OTHER_HOTEL = uuid.uuid4()
CASHIER_ID = uuid.uuid4()
ADMIN_ID = uuid.uuid4()
CASHIER = {"user_type": "EMPLOYEE", "id": CASHIER_ID, "permissions": ["expense.create"], "hotel_id": HOTEL}
ADMIN = {"user_type": "ADMIN", "actual_user_type": "ADMIN", "id": ADMIN_ID, "permissions": [], "hotel_id": HOTEL}
TODAY = date(2026, 11, 21)
YESTERDAY = date(2026, 11, 20)
CREATED_AT = datetime(2026, 11, 20, 9, 30, tzinfo=timezone.utc)


def run(body, *, audit_table=True):
    async def main():
        engine = create_async_engine("sqlite+aiosqlite://")
        tables = [Hotel.__table__, Branch.__table__, User.__table__, Expense.__table__, ExpenseCategory.__table__]
        if audit_table:
            tables.append(AuditLog.__table__)
        async with engine.begin() as conn:
            await conn.run_sync(lambda s: Base.metadata.create_all(s, tables=tables))
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with maker() as session:
                session.add_all(
                    [
                        User(id=ADMIN_ID, first_name="Aziz", last_name="Admin", user_type="ADMIN",
                             username="admin", password_hash="x", hotel_id=None),
                        User(id=CASHIER_ID, first_name="Kamola", last_name="Kassir", user_type="EMPLOYEE",
                             username="kassir", password_hash="x", hotel_id=None),
                    ]
                )
                await session.flush()
                await body(session)
        finally:
            await engine.dispose()

    asyncio.run(main())


def base(**over) -> dict:
    data = {
        "title": "Gaz, noyabr", "amount": 100000.0, "category": "gaz",
        "payment_method": "CASH", "payments": None, "expense_date": YESTERDAY, "notes": None, "branch_id": None,
    }
    data.update(over)
    return data


async def create(session, **over) -> dict:
    """Kassir kiritgan xarajat — kechagi soat 09:30 da (smena vaqti)."""
    out = await ExpenseService(session).create(HOTEL, base(**over), CASHIER)
    for r in (await session.execute(select(Expense))).scalars().all():
        r.created_at = CREATED_AT
    await session.flush()
    return out


async def rows_of(session) -> list[Expense]:
    return list(
        (await session.execute(select(Expense).order_by(Expense.amount.desc()))).scalars().all()
    )


def same_moment(a: datetime, b: datetime) -> bool:
    # SQLite vaqt mintaqasini saqlamaydi — UTC deb solishtiramiz
    def norm(d: datetime) -> datetime:
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    return norm(a) == norm(b)


# ------------------------------------------------------------ huquq --


def test_only_admin_super_admin_and_configurator_can_edit():
    assert can_edit_expenses(ADMIN)
    assert can_edit_expenses({"user_type": "SUPER_ADMIN"})
    # Mehmonxona tanlagan sozlovchi so'rovda ADMIN bo'lib keladi
    assert can_edit_expenses({"user_type": "ADMIN", "actual_user_type": "CONFIGURATOR"})
    # Mehmonxona tanlamagan sozlovchi — yo'q
    assert not can_edit_expenses({"user_type": "CONFIGURATOR"})
    # O'chira oladigan xodim ham tahrirlay olmaydi
    assert not can_edit_expenses({"user_type": "EMPLOYEE", "permissions": ["expense.delete"]})
    assert not can_edit_expenses(CASHIER)
    assert not can_edit_expenses(None)

    async def body(session):
        out = await create(session)
        with pytest.raises(ForbiddenException) as e:
            await ExpenseService(session).update(
                HOTEL, uuid.UUID(out["id"]), {"amount": 5000},
                {**CASHIER, "permissions": ["expense.create", "expense.delete"]},
            )
        assert e.value.error_code == EDIT_FORBIDDEN_CODE
        assert float((await rows_of(session))[0].amount) == 100000.0

    run(body)


def test_configurator_edit_is_recorded_with_real_role():
    async def body(session):
        out = await create(session)
        configurator = {"user_type": "ADMIN", "actual_user_type": "CONFIGURATOR", "id": ADMIN_ID, "hotel_id": HOTEL}
        res = await ExpenseService(session).update(HOTEL, uuid.UUID(out["id"]), {"amount": 90000}, configurator)
        assert res["edit"]["by_role"] == "CONFIGURATOR"
        res = await ExpenseService(session).update(None, uuid.UUID(out["id"]), {"amount": 80000}, {"user_type": "SUPER_ADMIN", "id": ADMIN_ID})
        assert res["edit"]["by_role"] == "SUPER_ADMIN"
        assert [h["by_role"] for h in res["edit_history"]] == ["CONFIGURATOR", "SUPER_ADMIN"]

    run(body)


def test_other_hotel_and_unknown_expense_are_not_found():
    async def body(session):
        out = await create(session)
        service = ExpenseService(session)
        with pytest.raises(NotFoundException):
            await service.update(OTHER_HOTEL, uuid.UUID(out["id"]), {"amount": 1}, ADMIN)
        with pytest.raises(NotFoundException):
            await service.update(HOTEL, uuid.uuid4(), {"amount": 1}, ADMIN)

    run(body)


# ------------------------------------------------------ oddiy xarajat --


def test_edit_changes_fields_but_keeps_time_and_author():
    async def body(session):
        await ExpenseCategoryService(session).list_categories(HOTEL)  # standart toifalar
        out = await create(session)
        service = ExpenseService(session)
        res = await service.update(
            HOTEL,
            uuid.UUID(out["id"]),
            {
                "title": "  Gaz, oktyabr ",
                "amount": 85000,
                "category": "suv",
                "expense_date": TODAY,
                "notes": " hisob-faktura №12 ",
                "note": "Summa noto'g'ri yozilgan edi",
            },
            ADMIN,
        )
        assert res["id"] == out["id"]
        assert res["title"] == "Gaz, oktyabr" and res["amount"] == 85000.0
        assert res["category"] == "Suv"  # ro'yxatdagi yozilishi
        assert res["expense_date"] == str(TODAY) and res["notes"] == "hisob-faktura №12"
        assert res["payment_method"] == "CASH" and res["split_group"] is None
        # Kim kiritgani o'zgarmaydi
        assert res["created_by"] == str(CASHIER_ID) and res["created_by_name"] == "Kamola Kassir"

        [row] = await rows_of(session)
        assert str(row.id) == out["id"] and row.created_by == CASHIER_ID
        assert same_moment(row.created_at, CREATED_AT)  # smena oynasida qoladi

        edit = res["edit"]
        assert edit["by"] == str(ADMIN_ID) and edit["by_name"] == "Aziz Admin" and edit["by_role"] == "ADMIN"
        assert edit["note"] == "Summa noto'g'ri yozilgan edi"
        assert set(edit["changes"]) == {"amount", "category", "title", "expense_date", "notes"}
        assert edit["changes"]["amount"] == {"old": 100000.0, "new": 85000.0}
        assert edit["changes"]["category"] == {"old": "Gaz", "new": "Suv"}
        assert edit["changes"]["expense_date"] == {"old": str(YESTERDAY), "new": str(TODAY)}
        assert res["edit_history"] == [edit] and row.edit_history == [edit]

        # Audit jurnali
        [log] = (await session.execute(select(AuditLog))).scalars().all()
        assert log.action == "UPDATE" and log.entity_type == "expense" and str(log.entity_id) == out["id"]
        assert log.old_values["amount"] == 100000.0 and log.new_values["amount"] == 85000.0
        assert sorted(log.new_values["changes"]) == sorted(edit["changes"])

    run(body)


def test_only_sent_fields_change_and_empty_edit_is_refused():
    async def body(session):
        out = await create(session, notes="eski izoh")
        service = ExpenseService(session)
        res = await service.update(HOTEL, uuid.UUID(out["id"]), {"payment_method": "CARD"}, ADMIN)
        assert res["payment_method"] == "CARD" and res["amount"] == 100000.0
        assert res["notes"] == "eski izoh" and res["title"] == "Gaz, noyabr"
        assert list(res["edit"]["changes"]) == ["payment"]
        assert res["edit"]["changes"]["payment"] == {
            "old": [{"payment_method": "CASH", "amount": 100000.0}],
            "new": [{"payment_method": "CARD", "amount": 100000.0}],
        }

        # Izohni o'chirish (bo'sh) — o'zgarish; toifani olib tashlash ham
        res = await service.update(HOTEL, uuid.UUID(out["id"]), {"notes": "  ", "category": None}, ADMIN)
        assert res["notes"] is None and res["category"] is None
        assert set(res["edit"]["changes"]) == {"notes", "category"}

        # Hech narsa o'zgarmasa — xato, tarixga yozilmaydi
        for same in ({}, {"amount": 100000}, {"title": "Gaz, noyabr", "payment_method": "CARD"}, {"note": "faqat izoh"}):
            with pytest.raises(ValidationException) as e:
                await service.update(HOTEL, uuid.UUID(out["id"]), same, ADMIN)
            assert e.value.error_code == "EXPENSE_UNCHANGED"
        assert len((await rows_of(session))[0].edit_history) == 2

        with pytest.raises(ValidationException) as e:
            await service.update(HOTEL, uuid.UUID(out["id"]), {"title": "   "}, ADMIN)
        assert e.value.error_code == "TITLE_REQUIRED"

    run(body)


def test_history_accumulates_and_is_capped():
    async def body(session):
        out = await create(session)
        service = ExpenseService(session)
        for i in range(MAX_HISTORY + 3):
            await service.update(HOTEL, uuid.UUID(out["id"]), {"amount": 1000 + i}, ADMIN)
        [row] = await rows_of(session)
        assert len(row.edit_history) == MAX_HISTORY
        # Eng eskilari tushib qoladi, oxirgisi oxirida
        assert row.edit_history[-1]["changes"]["amount"]["new"] == float(1000 + MAX_HISTORY + 2)

    run(body)


# --------------------------------------------------- bo'lingan xarajat --


def test_single_expense_can_be_split_keeping_time_and_author():
    async def body(session):
        out = await create(session)
        res = await ExpenseService(session).update(
            HOTEL,
            uuid.UUID(out["id"]),
            {"payments": [{"payment_method": "CASH", "amount": 60000}, {"payment_method": "ONLINE", "amount": 40000}]},
            ADMIN,
        )
        assert res["split_group"] and len(res["parts"]) == 2
        rows = await rows_of(session)
        assert [(r.payment_method, float(r.amount)) for r in rows] == [("CASH", 60000.0), ("ONLINE", 40000.0)]
        # Naqd qator — o'sha qator (id saqlanadi), online — yangi qator
        assert str(rows[0].id) == out["id"]
        assert {r.split_group for r in rows} == {uuid.UUID(res["split_group"])}
        # Yangi bo'lak ham kassirning, o'sha paytdagi xarajati
        assert all(r.created_by == CASHIER_ID for r in rows)
        assert all(same_moment(r.created_at, CREATED_AT) for r in rows)
        assert all(r.edit_history == rows[0].edit_history for r in rows)
        assert {r.title for r in rows} == {"Gaz, noyabr"} and {r.expense_date for r in rows} == {YESTERDAY}

    run(body)


def test_split_expense_is_edited_as_a_whole_through_any_part():
    async def body(session):
        out = await create(
            session,
            payments=[{"payment_method": "CASH", "amount": 60000}, {"payment_method": "ONLINE", "amount": 40000}],
        )
        cash_id, online_id = out["parts"][0]["id"], out["parts"][1]["id"]
        service = ExpenseService(session)

        # Ikkinchi bo'lak id si bilan — butun xarajat nomi o'zgaradi
        res = await service.update(HOTEL, uuid.UUID(online_id), {"title": "Gaz va suv"}, ADMIN)
        assert {p["title"] for p in res["parts"]} == {"Gaz va suv"}
        assert res["id"] == online_id  # so'ralgan qator javobning asosiy qatori

        # Summa o'zgarsa bo'laklarsiz — xato
        with pytest.raises(ValidationException) as e:
            await service.update(HOTEL, uuid.UUID(cash_id), {"amount": 90000}, ADMIN)
        assert e.value.error_code == "PARTS_REQUIRED"
        # Bo'laklar yig'indisi teng bo'lmasa — xato, hech narsa o'zgarmaydi
        with pytest.raises(ValidationException) as e:
            await service.update(
                HOTEL, uuid.UUID(cash_id),
                {"amount": 90000, "payments": [{"payment_method": "CASH", "amount": 60000}, {"payment_method": "CARD", "amount": 20000}]},
                ADMIN,
            )
        assert e.value.error_code == "PARTS_SUM_MISMATCH"
        assert sorted(float(r.amount) for r in await rows_of(session)) == [40000.0, 60000.0]

        # Online → karta, summa 90 000: naqd qator saqlanadi, ikkinchisi qayta ishlatiladi
        res = await service.update(
            HOTEL, uuid.UUID(cash_id),
            {"amount": 90000, "payments": [{"payment_method": "CASH", "amount": 50000}, {"payment_method": "CARD", "amount": 40000}]},
            ADMIN,
        )
        rows = await rows_of(session)
        assert [(r.payment_method, float(r.amount)) for r in rows] == [("CASH", 50000.0), ("CARD", 40000.0)]
        assert {str(r.id) for r in rows} == {cash_id, online_id}
        assert res["edit"]["changes"]["amount"] == {"old": 100000.0, "new": 90000.0}
        assert len(rows[0].edit_history) == 2

        # Bitta turga qaytarish: guruh yo'qoladi, ortiqcha qator o'chadi
        res = await service.update(HOTEL, uuid.UUID(online_id), {"payment_method": "CARD"}, ADMIN)
        [row] = await rows_of(session)
        assert row.payment_method == "CARD" and float(row.amount) == 90000.0 and row.split_group is None
        assert str(row.id) == online_id and res["split_group"] is None and "parts" not in res
        assert len(row.edit_history) == 3 and same_moment(row.created_at, CREATED_AT)

    run(body)


def test_split_title_change_keeps_parts_when_amount_is_the_same():
    async def body(session):
        out = await create(
            session,
            payments=[{"payment_method": "CASH", "amount": 70000}, {"payment_method": "CARD", "amount": 30000}],
        )
        res = await ExpenseService(session).update(
            HOTEL, uuid.UUID(out["id"]), {"amount": 100000, "expense_date": TODAY}, ADMIN
        )
        assert list(res["edit"]["changes"]) == ["expense_date"]
        rows = await rows_of(session)
        assert [(r.payment_method, float(r.amount)) for r in rows] == [("CASH", 70000.0), ("CARD", 30000.0)]
        assert {r.expense_date for r in rows} == {TODAY}

    run(body)


def test_reports_follow_the_edit():
    async def body(session):
        await ExpenseCategoryService(session).list_categories(HOTEL)
        out = await create(session)
        await ExpenseService(session).update(
            HOTEL, uuid.UUID(out["id"]),
            {"amount": 120000, "category": "Suv", "payments": [{"payment_method": "CASH", "amount": 20000}, {"payment_method": "CARD", "amount": 100000}]},
            ADMIN,
        )
        store: dict = {}

        def bucket(method):
            return store.setdefault(method, {"expense": 0.0})

        summary = await FinanceSummaryService(session)._expenses(HOTEL, YESTERDAY, YESTERDAY, bucket)
        assert summary["expense_total"] == 120000.0 and summary["expense_count"] == 1
        assert {c["name"] for c in summary["expense_categories"]} == {"Suv"}
        assert store["CASH"]["expense"] == 20000.0 and store["CARD"]["expense"] == 100000.0

    run(body)


# ------------------------------------------------------------- audit --


def test_audit_failure_does_not_break_the_edit():
    async def body(session):
        out = await create(session)
        # audit_logs jadvali yo'q — yozuv xato beradi, tahrir esa saqlanadi
        res = await ExpenseService(session).update(HOTEL, uuid.UUID(out["id"]), {"amount": 7000}, ADMIN)
        assert res["amount"] == 7000.0
        await session.commit()
        [row] = await rows_of(session)
        assert float(row.amount) == 7000.0 and len(row.edit_history) == 1

    run(body, audit_table=False)


def test_diff_and_dict_shapes():
    before = {"title": "a", "amount": 10.0, "category": None, "payment": [], "expense_date": "2026-11-21", "notes": None}
    assert diff_snapshots(before, {**before, "amount": 10.001}) == {}
    assert diff_snapshots(before, {**before, "notes": "x"}) == {"notes": {"old": None, "new": "x"}}

    row = Expense(
        id=uuid.uuid4(), hotel_id=HOTEL, title="t", category=None, amount=10, payment_method="CASH",
        expense_date=TODAY, created_by=CASHIER_ID, split_group=None, edit_history=None,
    )
    assert expense_dict(row, None)["edit_history"] == []
    row.edit_history = [{"at": "x"}]
    assert expense_dict(row, None)["edit_history"] == [{"at": "x"}]


def test_route_is_registered_with_validation():
    from app.presentation.api.v1.expenses import ExpenseUpdateRequest, router

    methods = {(tuple(sorted(r.methods)), r.path) for r in router.routes}
    assert (("PUT",), "/{expense_id}") in methods
    data = ExpenseUpdateRequest(amount=5, note="sabab").model_dump(exclude_unset=True)
    assert data == {"amount": 5.0, "note": "sabab"}
    with pytest.raises(ValueError):
        ExpenseUpdateRequest(payment_method="BITCOIN")
    with pytest.raises(ValueError):
        ExpenseUpdateRequest(amount=0)
