"""Avto-chiqishda xona yetim CLEANING da qolmasligi.

Nega kerak: farrosh 5-daqiqalik vazifani chiqish vaqtidan OLDIN yakunlasa
(mehmon erta ketgan, qisqa soatlik bron), yakunlash hook'i xonaga tegmaydi —
u hali OCCUPIED. Chiqish vaqti kelganda 2-bosqich xonani CLEANING qiladi,
3-bosqich bronni yopadi, xona esa faol vazifasiz CLEANING da qolib ketardi
(102-xona holati). Endi 3-bosqich "tozalandi" deb yopganda xonani xo'jalik
xizmatining bo'shatish qoidasidan o'tkazadi.

Bazasiz: session/repo/servislar o'rnida soddalashtirilgan o'rinbosarlar.
"""
import asyncio
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.application.services import housekeeping_service as hk
from app.application.services.automation_service import AutomationService


class FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


class FakeRoomRepo:
    def __init__(self, room):
        self.room = room

    async def get_by_id(self, room_id, hotel_id):
        return self.room

    async def update(self, room, **values):
        for k, v in values.items():
            setattr(room, k, v)
        return room


class FakeReservationService:
    def __init__(self):
        self.checkouts = []

    async def check_out(self, reservation_id, hotel_id, actor, **kw):
        self.checkouts.append((reservation_id, kw))


def build(task_status: str, monkeypatch):
    hotel_id, room_id = uuid.uuid4(), uuid.uuid4()
    room = SimpleNamespace(id=room_id, hotel_id=hotel_id, current_status="OCCUPIED")
    reservation = SimpleNamespace(
        id=uuid.uuid4(),
        hotel_id=hotel_id,
        room_id=room_id,
        branch_id=uuid.uuid4(),
        status="CHECKED_IN",
        reservation_number="RES-TEST",
        created_by=uuid.uuid4(),
        check_out_datetime=datetime(2026, 9, 10, 12, 26),
        check_out_date=datetime(2026, 9, 10).date(),
    )
    task = SimpleNamespace(
        id=uuid.uuid4(), room_id=room_id, task_type="CLEANING", status=task_status
    )

    svc = AutomationService.__new__(AutomationService)
    svc.session = FakeSession()
    svc.room_repo = FakeRoomRepo(room)
    svc.res_service = FakeReservationService()

    async def linked(self, reservation_id):
        return task

    released = []

    async def fake_release(self, t, h_id, user_id):
        released.append((t, h_id, user_id))

    monkeypatch.setattr(AutomationService, "_linked_cleaning_task", linked)
    monkeypatch.setattr(hk.HousekeepingService, "_release_task_room_status", fake_release)
    return svc, reservation, room, task, released


def test_early_completed_task_releases_room_on_auto_checkout(monkeypatch):
    """Aynan 102-xona holati: vazifa 12:24 da yopilgan, chiqish 12:26."""
    svc, reservation, room, task, released = build("COMPLETED", monkeypatch)
    now = datetime(2026, 9, 10, 12, 26, 30)  # chiqish vaqti keldi

    asyncio.run(svc._process(reservation, now))

    # 2-bosqich xonani CLEANING qildi, 3-bosqich bronni yopdi...
    assert room.current_status == "CLEANING"
    assert len(svc.res_service.checkouts) == 1
    # ...va xonani bo'shatish qoidasidan o'tkazdi — yetim qolmaydi
    assert released == [(task, reservation.hotel_id, reservation.created_by)]


def test_timeout_checkout_leaves_room_for_the_open_task(monkeypatch):
    """Tozalash tugamagan, muhlat o'tgan: xona farrosh uchun CLEANING qoladi."""
    svc, reservation, room, task, released = build("OPEN", monkeypatch)
    monkeypatch.setattr(
        "app.application.services.automation_service.settings.AUTO_CHECKOUT_GRACE_MINUTES", 0
    )
    now = datetime(2026, 9, 10, 12, 27)

    asyncio.run(svc._process(reservation, now))

    assert len(svc.res_service.checkouts) == 1
    assert released == []  # ochiq vazifa bor — xonaga tegilmaydi


def test_before_checkout_moment_nothing_is_closed(monkeypatch):
    svc, reservation, room, task, released = build("COMPLETED", monkeypatch)
    now = datetime(2026, 9, 10, 12, 20)  # chiqishga 6 daqiqa bor

    asyncio.run(svc._process(reservation, now))

    assert svc.res_service.checkouts == []
    assert released == []
    assert room.current_status == "OCCUPIED"


@pytest.mark.parametrize("status", ["OPEN", "IN_PROGRESS"])
def test_unfinished_task_within_grace_waits(monkeypatch, status):
    svc, reservation, room, task, released = build(status, monkeypatch)
    monkeypatch.setattr(
        "app.application.services.automation_service.settings.AUTO_CHECKOUT_GRACE_MINUTES", 30
    )
    now = datetime(2026, 9, 10, 12, 40)  # muhlat ichida

    asyncio.run(svc._process(reservation, now))

    assert svc.res_service.checkouts == []
    assert released == []
