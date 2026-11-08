"""Xarajat toifalari: ro'yxat, nom qoidalari, birinchi to'ldirish.

Ishga tushirish: pytest tests/test_expense_categories.py

Nozik joylar:

1. Toifa xarajatda NOM bilan saqlanadi. Toifa qayta nomlansa shu
   mehmonxonaning xarajatlaridagi nom ham o'zgaradi — hisobot bo'linmaydi.
   Boshqa mehmonxonaning bir xil nomli xarajati tegilmaydi.

2. Ishlatilgan toifa o'chirilmaydi — arxivga tushadi (eski xarajatlar o'z
   toifasida qoladi, qayta yoqsa bo'ladi). Ishlatilmagani butunlay o'chadi.
   Arxivdagi nom bilan yangi toifa yaratilsa — arxivdagi qayta yoqiladi.

3. Birinchi to'ldirish (migratsiya): toifasi yo'q mehmonxona standart
   ro'yxatni va ilgari qo'lda yozilgan nomlarini oladi; toifasi bor
   mehmonxonaga standart ro'yxat qayta qo'shilmaydi. Qayta ishga tushsa
   hech narsa o'zgarmaydi.

4. Nom taqqoslashda harf kattaligi va bo'shliqlar farq qilmaydi:
   "ish haqi" kiritilsa xarajatga "Ish haqi" yoziladi.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.services.expense_category_service import (
    ExpenseCategoryService,
    can_manage_categories,
)
from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.expense import Expense
from app.infrastructure.database.models.expense_category import ExpenseCategory
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.user import User
from app.shared.expense_categories import (
    COLORS,
    DEFAULT_CATEGORIES,
    EXPENSE_CATEGORIES,
    ICONS,
    MAX_NAME_LENGTH,
    OTHER_NAME,
    name_key,
    normalize_name,
    seed_expense_categories,
)

HOTEL = uuid.uuid4()
OTHER_HOTEL = uuid.uuid4()
ADMIN = {"user_type": "ADMIN", "id": uuid.uuid4(), "permissions": []}
SUPER = {"user_type": "SUPER_ADMIN", "id": uuid.uuid4(), "permissions": []}
MANAGER = {"user_type": "EMPLOYEE", "id": uuid.uuid4(), "permissions": ["expense.delete", "expense.create"]}
STAFF = {"user_type": "EMPLOYEE", "id": uuid.uuid4(), "permissions": ["expense.create"]}

# ----------------------------------------------------------- yordamchi --


def run(body):
    """Testni toza SQLite bazasi va ochiq sessiya bilan ishga tushiradi
    (loyihadagi boshqa testlar kabi asyncio.run orqali, plaginsiz)."""

    async def main():
        engine = create_async_engine("sqlite+aiosqlite://")
        # Branch — xarajat filialga bog'langan (BranchScoped), yozishda
        # asosiy filial qidiriladi
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


def expense(hotel_id, category, amount=10000, user_id=None):
    return Expense(
        id=uuid.uuid4(), hotel_id=hotel_id, title="x", category=category, amount=amount,
        payment_method="CASH", expense_date=date(2026, 11, 1), created_by=user_id or ADMIN["id"],
    )


async def names(service, hotel_id=HOTEL, include_archived=False):
    return [c["name"] for c in await service.list_categories(hotel_id, include_archived)]


# ------------------------------------------------------------ ruxsat --


def test_manage_rights_follow_expense_delete():
    assert can_manage_categories(ADMIN) and can_manage_categories(SUPER)
    assert can_manage_categories(MANAGER)
    assert not can_manage_categories(STAFF)
    assert not can_manage_categories(None)


def test_name_normalization_ignores_case_and_spaces():
    assert normalize_name("  Ish   haqi ") == "Ish haqi"
    assert name_key("ISH HAQI") == name_key("ish haqi") == name_key(" Ish  haqi ")
    assert name_key(None) == ""


def test_defaults_are_valid_and_unique():
    keys = [name_key(n) for n, _i, _c in DEFAULT_CATEGORIES]
    assert len(set(keys)) == len(keys)
    for name, icon, color in DEFAULT_CATEGORIES:
        assert 0 < len(name) <= MAX_NAME_LENGTH
        assert icon in ICONS and color in COLORS
    # Hisobotdagi "toifasiz" nomi ro'yxatning OXIRIDA turadi
    assert DEFAULT_CATEGORIES[-1][0] == OTHER_NAME


# ------------------------------------------------------------- servis --


def test_first_list_seeds_defaults_once():
    async def body(session):
        service = ExpenseCategoryService(session)
        got = await names(service)
        assert got == [n for n, _i, _c in DEFAULT_CATEGORIES]
        # Ikkinchi chaqiruv qayta yozmaydi
        again = await service.list_categories(HOTEL)
        assert len(again) == len(DEFAULT_CATEGORIES)
        assert again[0]["icon"] == "wallet" and again[0]["color"] == "emerald"
        assert again[0]["usage_count"] == 0 and again[0]["usage_total"] == 0
        # Boshqa mehmonxona o'ziniki bilan
        assert await names(service, OTHER_HOTEL) == got
        total = (await session.execute(sa.select(sa.func.count()).select_from(ExpenseCategory))).scalar()
        assert total == 2 * len(DEFAULT_CATEGORIES)

    run(body)


def test_list_reports_usage_hotel_wide():
    async def body(session):
        service = ExpenseCategoryService(session)
        await service.list_categories(HOTEL)
        session.add_all([
            expense(HOTEL, "Gaz", 50000), expense(HOTEL, "gaz", 25000),
            expense(HOTEL, "Boshqa mehmonxona emas", 1), expense(OTHER_HOTEL, "Gaz", 999999),
        ])
        await session.flush()
        gaz = next(c for c in await service.list_categories(HOTEL) if c["name"] == "Gaz")
        assert gaz["usage_count"] == 2 and gaz["usage_total"] == 75000

    run(body)


def test_create_validates_and_rejects_duplicates():
    async def body(session):
        service = ExpenseCategoryService(session)
        await service.list_categories(HOTEL)

        with pytest.raises(ForbiddenException):
            await service.create(HOTEL, {"name": "Kanstovar"}, STAFF)
        with pytest.raises(ValidationException):
            await service.create(HOTEL, {"name": "   "}, ADMIN)
        with pytest.raises(ValidationException):
            await service.create(HOTEL, {"name": "x" * (MAX_NAME_LENGTH + 1)}, ADMIN)
        with pytest.raises(ValidationException):
            await service.create(HOTEL, {"name": "Kanstovar", "icon": "rocket"}, ADMIN)
        with pytest.raises(ValidationException):
            await service.create(HOTEL, {"name": "Kanstovar", "color": "#ff0000"}, ADMIN)
        with pytest.raises(ConflictException):
            await service.create(HOTEL, {"name": " gaz "}, ADMIN)

        created = await service.create(HOTEL, {"name": " Kanstovar  buyumlari ", "icon": "package", "color": "sky"}, MANAGER)
        assert created["name"] == "Kanstovar buyumlari"
        assert created["icon"] == "package" and created["color"] == "sky" and created["is_active"]
        # Belgi/rang berilmasa standart qiymat
        plain = await service.create(HOTEL, {"name": "Qo'riqlash"}, ADMIN)
        assert plain["icon"] == "tag" and plain["color"] == "gray"
        # Yangi toifalar "Boshqa" dan OLDIN turadi
        got = await names(service)
        assert got[-1] == OTHER_NAME
        assert got[-3:-1] == ["Kanstovar buyumlari", "Qo'riqlash"]

    run(body)


def test_rename_cascades_to_expenses_of_this_hotel_only():
    async def body(session):
        service = ExpenseCategoryService(session)
        cats = await service.list_categories(HOTEL)
        gaz = next(c for c in cats if c["name"] == "Gaz")
        session.add_all([
            expense(HOTEL, "Gaz", 1), expense(HOTEL, "Gaz", 2), expense(HOTEL, "Suv", 3),
            expense(OTHER_HOTEL, "Gaz", 4),
        ])
        await session.flush()

        with pytest.raises(ForbiddenException):
            await service.update(HOTEL, uuid.UUID(gaz["id"]), {"name": "Tabiiy gaz"}, STAFF)
        with pytest.raises(ConflictException):
            await service.update(HOTEL, uuid.UUID(gaz["id"]), {"name": "suv"}, ADMIN)

        updated = await service.update(HOTEL, uuid.UUID(gaz["id"]), {"name": "Tabiiy gaz", "color": "orange"}, MANAGER)
        assert updated["name"] == "Tabiiy gaz" and updated["renamed_expenses"] == 2
        assert updated["usage_count"] == 2

        rows = (await session.execute(sa.select(Expense.hotel_id, Expense.category))).all()
        assert sorted((str(h), c) for h, c in rows) == sorted([
            (str(HOTEL), "Tabiiy gaz"), (str(HOTEL), "Tabiiy gaz"), (str(HOTEL), "Suv"), (str(OTHER_HOTEL), "Gaz"),
        ])
        # Faqat harf kattaligi o'zgarsa ham xarajatlar birga o'zgaradi
        same = await service.update(HOTEL, uuid.UUID(gaz["id"]), {"name": "Tabiiy GAZ"}, ADMIN)
        assert same["renamed_expenses"] == 2 and same["name"] == "Tabiiy GAZ"
        # Nomsiz yangilash — hech narsa qayta nomlanmaydi
        quiet = await service.update(HOTEL, uuid.UUID(gaz["id"]), {"icon": "flame"}, ADMIN)
        assert quiet["renamed_expenses"] == 0 and quiet["icon"] == "flame"

    run(body)


def test_delete_archives_used_and_removes_unused():
    async def body(session):
        service = ExpenseCategoryService(session)
        cats = await service.list_categories(HOTEL)
        gaz = next(c for c in cats if c["name"] == "Gaz")
        suv = next(c for c in cats if c["name"] == "Suv")
        session.add(expense(HOTEL, "Gaz", 100))
        await session.flush()

        with pytest.raises(ForbiddenException):
            await service.delete(HOTEL, uuid.UUID(gaz["id"]), STAFF)

        archived = await service.delete(HOTEL, uuid.UUID(gaz["id"]), ADMIN)
        assert archived["archived"] and not archived["deleted"] and archived["usage_count"] == 1
        removed = await service.delete(HOTEL, uuid.UUID(suv["id"]), MANAGER)
        assert removed["deleted"] and not removed["archived"]

        active = await names(service)
        assert "Gaz" not in active and "Suv" not in active
        everything = await service.list_categories(HOTEL, include_archived=True)
        assert [c["name"] for c in everything if not c["is_active"]] == ["Gaz"]
        # Xarajat o'z toifasida qoldi
        assert (await session.execute(sa.select(Expense.category))).scalar() == "Gaz"

        # Arxivdagi nom bilan "yangi" toifa — arxivdagi qayta yoqiladi, nusxa emas
        back = await service.create(HOTEL, {"name": "GAZ", "icon": "fuel"}, ADMIN)
        assert back["id"] == gaz["id"] and back["is_active"] and back["name"] == "GAZ" and back["icon"] == "fuel"
        assert len(await service.list_categories(HOTEL, include_archived=True)) == len(DEFAULT_CATEGORIES) - 1

        with pytest.raises(NotFoundException):
            await service.delete(HOTEL, uuid.uuid4(), ADMIN)
        with pytest.raises(NotFoundException):
            await service.delete(OTHER_HOTEL, uuid.UUID(gaz["id"]), ADMIN)

    run(body)


def test_reorder_puts_listed_first_and_keeps_the_rest():
    async def body(session):
        service = ExpenseCategoryService(session)
        cats = await service.list_categories(HOTEL)
        by_name = {c["name"]: uuid.UUID(c["id"]) for c in cats}
        with pytest.raises(ForbiddenException):
            await service.reorder(HOTEL, [by_name["Gaz"]], STAFF)
        with pytest.raises(NotFoundException):
            await service.reorder(HOTEL, [uuid.uuid4()], ADMIN)

        result = await service.reorder(HOTEL, [by_name["Gaz"], by_name["Suv"], by_name["Gaz"]], ADMIN)
        got = [c["name"] for c in result]
        assert got[:2] == ["Gaz", "Suv"]
        rest = [n for n, _i, _c in DEFAULT_CATEGORIES if n not in ("Gaz", "Suv")]
        assert got[2:] == rest
        assert [c["sort_order"] for c in result] == list(range(len(result)))

    run(body)


def test_resolve_name_uses_stored_spelling():
    async def body(session):
        service = ExpenseCategoryService(session)
        await service.list_categories(HOTEL)
        assert await service.resolve_name(HOTEL, " ish  HAQI ") == "Ish haqi"
        assert await service.resolve_name(HOTEL, "Yangi narsa") == "Yangi narsa"
        assert await service.resolve_name(HOTEL, "   ") is None
        assert await service.resolve_name(HOTEL, None) is None
        long = "a" * (MAX_NAME_LENGTH + 10)
        assert await service.resolve_name(HOTEL, long) == "a" * MAX_NAME_LENGTH
        # Arxivdagi toifa nomi ham tanilib qoladi (eski mijoz yuborsa)
        gaz = next(c for c in await service.list_categories(HOTEL) if c["name"] == "Gaz")
        await service.update(HOTEL, uuid.UUID(gaz["id"]), {"is_active": False}, ADMIN)
        assert await service.resolve_name(HOTEL, "gaz") == "Gaz"

    run(body)


# ---------------------------------------------- birinchi to'ldirish --

A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
CORE = sa.MetaData()
HOTELS_T = sa.Table("hotels", CORE, sa.Column("id", sa.Uuid, primary_key=True))
EXPENSES_T = sa.Table(
    "expenses", CORE,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("hotel_id", sa.Uuid),
    sa.Column("category", sa.String),
)
CATEGORIES_T = sa.Table(
    "expense_categories", CORE,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("hotel_id", sa.Uuid),
    sa.Column("name", sa.String(50)),
    sa.Column("icon", sa.String(30)),
    sa.Column("color", sa.String(20)),
    sa.Column("sort_order", sa.Integer),
    sa.Column("is_active", sa.Boolean),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)


@pytest.fixture
def conn():
    engine = sa.create_engine("sqlite://")
    CORE.create_all(engine)
    with engine.begin() as c:
        yield c


def _cats(conn, hotel):
    return [
        (r.name, r.icon, r.color, r.sort_order, r.is_active)
        for r in conn.execute(
            sa.select(CATEGORIES_T).where(CATEGORIES_T.c.hotel_id == hotel).order_by(CATEGORIES_T.c.sort_order)
        ).all()
    ]


def test_seed_gives_defaults_and_imports_used_names(conn):
    conn.execute(sa.insert(HOTELS_T), [{"id": A}, {"id": B}, {"id": C}])
    conn.execute(sa.insert(EXPENSES_T), [
        {"hotel_id": A, "category": "Gaz"},             # standart bilan bir xil — takrorlanmaydi
        {"hotel_id": A, "category": " kommunal  to'lovlar "},
        {"hotel_id": A, "category": "Kommunal to'lovlar"},  # bir xil nomning boshqa yozilishi
        {"hotel_id": A, "category": "   "},             # bo'sh — tashlanadi
        {"hotel_id": A, "category": None},
        {"hotel_id": A, "category": "Avans"},
        {"hotel_id": B, "category": "Qo'riqlash"},
    ])
    result = seed_expense_categories(conn)
    assert result == {"hotels": 3, "defaults": 3 * len(DEFAULT_CATEGORIES), "imported": 3}

    a = _cats(conn, A)
    assert [c[0] for c in a[: len(DEFAULT_CATEGORIES)]] == [n for n, _i, _c in DEFAULT_CATEGORIES]
    imported = a[len(DEFAULT_CATEGORIES):]
    # Alifbo tartibida, standart belgi/rang bilan, tartib raqami davom etadi
    assert [(c[0], c[1], c[2]) for c in imported] == [("Avans", "tag", "gray"), ("kommunal to'lovlar", "tag", "gray")]
    assert [c[3] for c in a] == list(range(len(a)))
    assert all(c[4] for c in a)
    assert [c[0] for c in _cats(conn, B)][-1] == "Qo'riqlash"
    assert len(_cats(conn, C)) == len(DEFAULT_CATEGORIES)

    # Qayta ishga tushsa hech narsa o'zgarmaydi
    assert seed_expense_categories(conn) == {"hotels": 0, "defaults": 0, "imported": 0}
    assert _cats(conn, A) == a


def test_seed_does_not_readd_defaults_to_a_hotel_that_already_has_categories(conn):
    conn.execute(sa.insert(HOTELS_T), [{"id": A}])
    conn.execute(sa.insert(CATEGORIES_T), [{
        "id": uuid.uuid4(), "hotel_id": A, "name": "Faqat shu", "icon": "tag", "color": "gray",
        "sort_order": 7, "is_active": True,
    }])
    conn.execute(sa.insert(EXPENSES_T), [
        {"hotel_id": A, "category": "faqat SHU"},   # bor — takrorlanmaydi
        {"hotel_id": A, "category": "Yangi nom"},
    ])
    assert seed_expense_categories(conn) == {"hotels": 1, "defaults": 0, "imported": 1}
    assert _cats(conn, A) == [
        ("Faqat shu", "tag", "gray", 7, True),
        ("Yangi nom", "tag", "gray", 8, True),
    ]


def test_seed_truncates_long_imported_names(conn):
    conn.execute(sa.insert(HOTELS_T), [{"id": A}])
    conn.execute(sa.insert(EXPENSES_T), [{"hotel_id": A, "category": "n" * 80}])
    seed_expense_categories(conn)
    assert max(len(c[0]) for c in _cats(conn, A)) == MAX_NAME_LENGTH


# ------------------------------------------- model va migratsiya mosligi --


def test_core_table_matches_orm_model():
    orm = {c.name for c in ExpenseCategory.__table__.columns}
    core = {c.name for c in EXPENSE_CATEGORIES.columns}
    assert core <= orm
    assert {"created_by"} == orm - core


def test_unique_name_index_compiles_for_postgresql():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex

    index = next(i for i in ExpenseCategory.__table__.indexes if i.name == "ux_expense_categories_hotel_name")
    sql = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "UNIQUE" in sql and "lower(name)" in sql and "hotel_id" in sql
