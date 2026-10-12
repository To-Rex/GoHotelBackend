"""Fayllar filial izolyatsiyasi: egasi boshqa filialda bo'lsa ko'rinmaydi.

`_assert_entity_visible` — fayl bog'langan obyekt (vazifa, muammo, xarajat,
skan, feedback) joriy filialda bormi. Filial filtri `session.get` orqali
avtomatik ishlaydi; bu yerda sessiya "boshqa filialda topilmadi" holatini
None bilan taqlid qiladi.
"""
import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.core.exceptions import NotFoundException
from app.presentation.api.v1.files import _assert_entity_visible

HOTEL = uuid.uuid4()


class FakeSession:
    def __init__(self, row):
        self.row = row
        self.calls = 0

    async def get(self, model, pk):
        self.calls += 1
        return self.row


def user(user_type="EMPLOYEE"):
    return {"id": str(uuid.uuid4()), "user_type": user_type, "hotel_id": str(HOTEL)}


def entity(hotel_id=HOTEL):
    return SimpleNamespace(hotel_id=hotel_id)


def test_entity_invisible_in_branch_gives_404():
    session = FakeSession(None)  # boshqa filial — filtrlab topilmadi
    with pytest.raises(NotFoundException) as exc:
        asyncio.run(
            _assert_entity_visible(
                session, "task", uuid.uuid4(), HOTEL, user()
            )
        )
    assert exc.value.error_code == "ENTITY_NOT_FOUND"


def test_same_branch_entity_is_allowed():
    session = FakeSession(entity())
    asyncio.run(_assert_entity_visible(session, "task_report", uuid.uuid4(), HOTEL, user()))
    assert session.calls == 1


def test_other_hotel_entity_gives_404():
    session = FakeSession(entity(hotel_id=uuid.uuid4()))
    with pytest.raises(NotFoundException):
        asyncio.run(
            _assert_entity_visible(session, "expense", uuid.uuid4(), HOTEL, user())
        )


def test_global_entity_types_are_not_touched():
    session = FakeSession(None)
    asyncio.run(_assert_entity_visible(session, "guest", uuid.uuid4(), HOTEL, user()))
    asyncio.run(_assert_entity_visible(session, "user", uuid.uuid4(), HOTEL, user()))
    assert session.calls == 0  # tekshirilmaydi — mehmon/xodim global


def test_super_admin_is_exempt():
    session = FakeSession(None)
    asyncio.run(
        _assert_entity_visible(session, "task", uuid.uuid4(), HOTEL, user("SUPER_ADMIN"))
    )
    assert session.calls == 0


def test_allow_missing_permits_orphan_cleanup():
    session = FakeSession(None)
    asyncio.run(
        _assert_entity_visible(
            session, "task", uuid.uuid4(), HOTEL, user(), allow_missing=True
        )
    )
