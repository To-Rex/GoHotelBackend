"""Xodim hisobotiga kirish (report_access).

Ishga tushirish: pytest tests/test_report_access.py

1. Har kim o'z hisobotini ko'radi — `user_id` bo'sh yoki o'ziniki.
2. Boshqa xodimning hisoboti — administrator va menejer (finance.view /
   shift.force_close / report.view) uchun; oddiy xodimga 403.
3. Boshqa mehmonxonaning yoki o'chirilgan xodimi — 404.
4. Xodimlar ro'yxati: so'rovchi birinchi, keyin faollar, keyin qolganlar;
   boshqa mehmonxona va o'chirilganlar chiqmaydi; oddiy xodimga 403.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.services.report_access import (
    FORBIDDEN_CODE,
    resolve_report_user,
    staff_options,
    user_brief,
)
from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.user import User

HOTEL = uuid.uuid4()
OTHER_HOTEL = uuid.uuid4()


def make_user(hotel_id, first, last, user_type="EMPLOYEE", status="ACTIVE", deleted=False):
    return User(
        id=uuid.uuid4(), hotel_id=hotel_id, user_type=user_type, username=f"{first}.{last}".lower() + uuid.uuid4().hex[:4],
        password_hash="x", first_name=first, last_name=last, status=status, is_deleted=deleted,
    )


def token(user: User, permissions=()):
    return {"id": user.id, "user_type": user.user_type, "hotel_id": user.hotel_id, "permissions": list(permissions)}


def run(body):
    async def main():
        engine = create_async_engine("sqlite+aiosqlite://")
        tables = [Hotel.__table__, Branch.__table__, User.__table__]
        async with engine.begin() as conn:
            await conn.run_sync(lambda s: Base.metadata.create_all(s, tables=tables))
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with maker() as session:
                admin = make_user(HOTEL, "Bobur", "Admin", user_type="ADMIN")
                manager = make_user(HOTEL, "Dilnoza", "Karimova")
                aziz = make_user(HOTEL, "Aziz", "Rahimov")
                gone = make_user(HOTEL, "Ketgan", "Xodim", status="INACTIVE")
                deleted = make_user(HOTEL, "O'chgan", "Xodim", deleted=True)
                stranger = make_user(OTHER_HOTEL, "Boshqa", "Mehmonxona")
                session.add_all([admin, manager, aziz, gone, deleted, stranger])
                await session.flush()
                await body(session, dict(admin=admin, manager=manager, aziz=aziz, gone=gone, deleted=deleted, stranger=stranger))
        finally:
            await engine.dispose()

    asyncio.run(main())


def test_everyone_sees_their_own_report():
    async def body(session, u):
        staff = token(u["aziz"], ["reservation.create"])
        user_id, user = await resolve_report_user(session, staff, HOTEL, None)
        assert user_id == u["aziz"].id and user.id == u["aziz"].id
        user_id, _ = await resolve_report_user(session, staff, HOTEL, u["aziz"].id)
        assert user_id == u["aziz"].id
        # Token id satr bo'lsa ham
        user_id, _ = await resolve_report_user(session, {**staff, "id": str(u["aziz"].id)}, HOTEL, None)
        assert user_id == u["aziz"].id

    run(body)


def test_only_admin_and_manager_see_others():
    async def body(session, u):
        staff = token(u["aziz"], ["reservation.create", "expense.create"])
        with pytest.raises(ForbiddenException) as e:
            await resolve_report_user(session, staff, HOTEL, u["manager"].id)
        assert e.value.error_code == FORBIDDEN_CODE

        for viewer in (
            token(u["admin"]),
            token(u["manager"], ["shift.force_close"]),
            token(u["manager"], ["finance.view"]),
            token(u["manager"], ["report.view"]),
            {"id": uuid.uuid4(), "user_type": "SUPER_ADMIN", "hotel_id": None, "permissions": []},
        ):
            user_id, user = await resolve_report_user(session, viewer, HOTEL, u["aziz"].id)
            assert user_id == u["aziz"].id and user.first_name == "Aziz"

    run(body)


def test_other_hotel_and_deleted_users_are_not_found():
    async def body(session, u):
        admin = token(u["admin"])
        with pytest.raises(NotFoundException):
            await resolve_report_user(session, admin, HOTEL, u["stranger"].id)
        with pytest.raises(NotFoundException):
            await resolve_report_user(session, admin, HOTEL, u["deleted"].id)
        with pytest.raises(NotFoundException):
            await resolve_report_user(session, admin, HOTEL, uuid.uuid4())
        # Tizim ma'muri mehmonxona tanlamagan (hotel_id None) — istalgan mehmonxona xodimi
        root = {"id": uuid.uuid4(), "user_type": "SUPER_ADMIN", "hotel_id": None, "permissions": []}
        user_id, _ = await resolve_report_user(session, root, None, u["stranger"].id)
        assert user_id == u["stranger"].id
        # Faol bo'lmagan xodimning hisoboti ko'riladi (o'tgan davr uchun)
        user_id, _ = await resolve_report_user(session, admin, HOTEL, u["gone"].id)
        assert user_id == u["gone"].id

    run(body)


def test_staff_options_order_and_scope():
    async def body(session, u):
        with pytest.raises(ForbiddenException):
            await staff_options(session, token(u["aziz"], ["reservation.create"]), HOTEL)

        rows = await staff_options(session, token(u["manager"], ["shift.force_close"]), HOTEL)
        names = [r["name"] for r in rows]
        # Men birinchi, keyin faollar ism bo'yicha, oxirida faol bo'lmagan;
        # o'chirilgan va boshqa mehmonxona yo'q
        assert names == ["Dilnoza Karimova", "Aziz Rahimov", "Bobur Admin", "Ketgan Xodim"]
        assert rows[0]["is_me"] is True and all(r["is_me"] is False for r in rows[1:])
        assert rows[-1]["status"] == "INACTIVE"
        assert {r["user_type"] for r in rows} == {"EMPLOYEE", "ADMIN"}

        # Tizim ma'muri (mehmonxonada yo'q) — o'zi ro'yxatga qo'shiladi, birinchi
        root = make_user(None, "Tizim", "Mamuri", user_type="SUPER_ADMIN")
        session.add(root)
        await session.flush()
        rows = await staff_options(session, token(root), HOTEL)
        assert rows[0]["name"] == "Tizim Mamuri" and rows[0]["is_me"]
        assert len(rows) == 5

    run(body)


def test_user_brief_shape():
    user = make_user(HOTEL, " Aziz ", "Rahimov ")
    brief = user_brief(user, user.id)
    assert brief["name"] == "Aziz Rahimov" and brief["is_me"] is True
    assert brief["first_name"] == "Aziz" and brief["user_type"] == "EMPLOYEE"
    assert user_brief(user, None)["is_me"] is False
