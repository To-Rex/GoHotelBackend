"""Mehmon dublikati: mavjud mehmon qaytariladi, javob buzilmaydi, qidiruv.

Ishga tushirish: pytest tests/test_guest_dedupe.py

Muammo (2026-11-09, prod): passport bo'yicha mavjud mehmon bo'lganda
POST /guests/ 500 qaytarardi — `updated_at` serverda yangilanadi,
flush'dan keyin eskiradi va javob seriyalanayotganda sinxron joyda
lazy-load bo'lib MissingGreenlet berardi. Endi flush'dan keyin refresh.

1. Yangi mehmon — `existing=False`; o'sha passport (boshqa formatda) bilan
   qayta yuborilsa — yangisi yaratilmaydi, `existing=True`, bo'sh maydonlar
   to'ldiriladi, mavjud ma'lumot ustiga yozilmaydi, `updated_at` o'qiladi.
2. Qidiruv (`find_duplicates`): passport mosligi birinchi, telefon oxirgi
   9 raqam bo'yicha, bir mehmon bir marta, qisqa qiymatlar e'tiborsiz.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import date

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.services.guest_service import GuestService
from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.branch import Branch
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.repositories.guest_repo import GuestRepository

HOTEL = uuid.uuid4()


def _sqlite_functions(dbapi_conn, _record):
    """PostgreSQL funksiyalarining SQLite o'rinbosarlari (faqat testda)."""
    dbapi_conn.create_function(
        "regexp_replace", 4, lambda value, pattern, repl, _flags: re.sub(pattern, repl, value or "")
    )
    dbapi_conn.create_function("right", 2, lambda value, n: (value or "")[-int(n):] if n else "")


def run(body):
    async def main():
        engine = create_async_engine("sqlite+aiosqlite://")
        event.listen(engine.sync_engine, "connect", _sqlite_functions)
        tables = [Hotel.__table__, Branch.__table__, Guest.__table__]
        async with engine.begin() as conn:
            await conn.run_sync(lambda s: Base.metadata.create_all(s, tables=tables))
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with maker() as session:
                await body(session)
        finally:
            await engine.dispose()

    asyncio.run(main())


def payload(**over):
    data = {
        "first_name": "SHOHISTA", "last_name": "ABDUMALIKOVA", "phone": "+998881534433",
        "passport_number": "AD4469339", "birth_date": date(2004, 5, 29), "nationality": "O'zbekiston",
        "email": None, "id_document_type": None, "id_document_number": None, "address": None, "notes": None,
    }
    data.update(over)
    return data


def test_existing_guest_is_returned_and_serializable():
    async def body(session):
        service = GuestService(session)
        first, existing = await service.create_or_match(HOTEL, payload(nationality=None))
        assert existing is False and first.passport_number == "AD4469339"

        # O'sha passport — boshqa yozilishda, qo'shimcha ma'lumot bilan
        again, existing = await service.create_or_match(
            HOTEL, payload(passport_number="ad 446-9339", phone="+998 90 000 00 00", nationality="O'zbekiston", address="Toshkent")
        )
        assert existing is True and again.id == first.id
        # Bo'sh maydonlar to'ldirildi, mavjudlari (telefon) ustiga yozilmadi
        assert again.nationality == "O'zbekiston" and again.address == "Toshkent"
        assert again.phone == "+998881534433"
        # Javob seriyalanishi: flush'dan keyin eskirgan ustun refresh bilan o'qilgan
        from app.application.dto.guest import GuestResponse

        response = GuestResponse.model_validate(again)
        assert response.updated_at is not None and response.existing is False
        response.existing = True
        assert response.model_dump()["existing"] is True

        # Faqat bitta mehmon bor
        assert len(await GuestRepository(session).get_all(HOTEL)) == 1

        # Eski imzo saqlangan
        same = await service.create_guest(HOTEL, payload())
        assert same.id == first.id

    run(body)


def test_phone_match_and_lookup_order():
    async def body(session):
        service = GuestService(session)
        a, _ = await service.create_or_match(HOTEL, payload())
        b, _ = await service.create_or_match(
            HOTEL, payload(first_name="AZIZ", last_name="RAHIMOV", passport_number="AB1234567", phone="998901112233")
        )
        assert a.id != b.id

        # Qidiruv: passport — a; telefon (boshqa formatda) — b; passport birinchi
        matches = await service.find_matches("ad 446 9339", "+998 (90) 111-22-33")
        assert [(g.id, by) for g, by in matches] == [(a.id, "passport"), (b.id, "phone")]

        # Faqat telefon, kodsiz 9 raqam — a
        by_phone = await service.find_matches(None, "881534433")
        assert [(g.id, by) for g, by in by_phone] == [(a.id, "phone")]

        # Bir mehmon ham passport, ham telefon bo'yicha mos kelsa — bir marta, passport deb
        both = await service.find_matches("AD4469339", "+998881534433")
        assert [(g.id, by) for g, by in both] == [(a.id, "passport")]

        # Qisqa qiymatlar — hech narsa
        assert await service.find_matches("AD4", "12345") == []
        assert await service.find_matches(None, None) == []

        # Telefon bo'yicha dublikat — yangi mehmon emas (passportsiz so'rov):
        # bu avvalgi qoida, o'zgarmagan
        again, existing = await service.create_or_match(
            HOTEL, payload(first_name="AZIZ", last_name="RAHIMOV", passport_number=None, phone="+998 90 111 22 33")
        )
        assert existing is True and again.id == b.id
        assert len(await GuestRepository(session).get_all(HOTEL)) == 2

    run(body)


def test_normalizers():
    assert GuestRepository.normalize_passport(" ad 446-9339 ") == "AD4469339"
    assert GuestRepository.normalize_passport(None) == ""
    assert GuestRepository.normalize_phone("+998 (88) 153-44-33") == "998881534433"
    assert GuestRepository.normalize_phone(None) == ""
