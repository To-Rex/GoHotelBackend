#!/usr/bin/env python3
"""Vazifa <-> xona holati qoidalari.

Ishga tushirish:  python tests/test_housekeeping_room_status.py

Nega kerak: xo'jalik vazifasi ochilganda xona holati o'zgarmasdi, shuning
uchun ta'mirdagi xona "Bo'sh" bo'lib turaverardi va unga bron qilish mumkin
edi — bron tekshiruvi xona holatiga qaraydi, vazifaga emas.

Faqat qaror mantig'i sinaladi: qaysi holatda xona egallanadi, qaysida yo'q,
va qachon bo'shatiladi. Baza bilan ishlash qismlari o'rniga soddalashtirilgan
o'rinbosarlar qo'yilgan.
"""
import asyncio
import os
import sys
import uuid
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.application.services.housekeeping_service import HousekeepingService


class FakeRoom:
    def __init__(self, status):
        self.id = uuid.uuid4()
        self.current_status = status


class FakeTask:
    def __init__(self, task_type, room_id, status="OPEN", scheduled_date=None):
        self.id = uuid.uuid4()
        self.task_type = task_type
        self.room_id = room_id
        self.status = status
        self.scheduled_date = scheduled_date


class FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class FakeSession:
    def __init__(self, other_active=None):
        self.other_active = other_active
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def execute(self, _stmt):
        return FakeResult((uuid.uuid4(),) if self.other_active else None)


class FakeRoomRepo:
    def __init__(self, room):
        self.room = room

    async def get_by_id(self, room_id, hotel_id):
        return self.room if self.room and self.room.id == room_id else None

    async def update(self, room, **values):
        for k, v in values.items():
            setattr(room, k, v)
        return room


def service(room, other_active=False):
    svc = HousekeepingService.__new__(HousekeepingService)
    svc.session = FakeSession(other_active)
    svc.room_repo = FakeRoomRepo(room)
    return svc


HOTEL, USER = uuid.uuid4(), uuid.uuid4()
today = date.today()
ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label:<52} {got}")
    else:
        fail += 1
        print(f"  XATO {label:<52} kutilgan {want}, chiqdi {got}")


print("--- vazifa ochilganda ---")
for task_type, start, want in [
    ("MAINTENANCE", "AVAILABLE", "MAINTENANCE"),
    ("INSPECTION", "AVAILABLE", "INSPECTION"),
    ("CLEANING", "AVAILABLE", "CLEANING"),
    ("DEEP_CLEANING", "AVAILABLE", "CLEANING"),
    # Mehmon ichkarida — tegilmaydi
    ("MAINTENANCE", "OCCUPIED", "OCCUPIED"),
    ("MAINTENANCE", "RESERVED", "RESERVED"),
    # Ataylab yopilgan xona yumshatilmaydi
    ("CLEANING", "OUT_OF_SERVICE", "OUT_OF_SERVICE"),
    # Xonani band qilmaydigan tur
    ("TURN_DOWN", "AVAILABLE", "AVAILABLE"),
]:
    room = FakeRoom(start)
    svc = service(room)
    task = FakeTask(task_type, room.id)
    asyncio.run(svc._apply_task_room_status(task, HOTEL, USER))
    check(f"{task_type} @ {start}", room.current_status, want)

print("--- rejalashtirilgan sana ---")
room = FakeRoom("AVAILABLE")
svc = service(room)
asyncio.run(
    svc._apply_task_room_status(
        FakeTask("MAINTENANCE", room.id, scheduled_date=today + timedelta(days=7)),
        HOTEL, USER,
    )
)
check("kelgusi haftaga rejalashtirilgan", room.current_status, "AVAILABLE")

room = FakeRoom("AVAILABLE")
svc = service(room)
asyncio.run(
    svc._apply_task_room_status(
        FakeTask("MAINTENANCE", room.id, scheduled_date=today), HOTEL, USER
    )
)
check("bugunga rejalashtirilgan", room.current_status, "MAINTENANCE")

print("--- vazifa yopilganda ---")
room = FakeRoom("MAINTENANCE")
svc = service(room, other_active=False)
asyncio.run(
    svc._release_task_room_status(
        FakeTask("MAINTENANCE", room.id, status="COMPLETED"), HOTEL, USER
    )
)
check("oxirgi vazifa yopildi", room.current_status, "AVAILABLE")

room = FakeRoom("MAINTENANCE")
svc = service(room, other_active=True)
asyncio.run(
    svc._release_task_room_status(
        FakeTask("MAINTENANCE", room.id, status="COMPLETED"), HOTEL, USER
    )
)
check("boshqa ochiq vazifa bor", room.current_status, "MAINTENANCE")

room = FakeRoom("MAINTENANCE")
svc = service(room, other_active=False)
asyncio.run(
    svc._release_task_room_status(
        FakeTask("MAINTENANCE", room.id, status="CANCELLED"), HOTEL, USER
    )
)
check("bekor qilindi", room.current_status, "AVAILABLE")

# Xona boshqa holatga o'tib ketgan bo'lsa tegilmaydi
room = FakeRoom("OCCUPIED")
svc = service(room, other_active=False)
asyncio.run(
    svc._release_task_room_status(
        FakeTask("MAINTENANCE", room.id, status="COMPLETED"), HOTEL, USER
    )
)
check("xona oralig'ida band bo'lib qolgan", room.current_status, "OCCUPIED")

print(f"\nJami: {ok} ok, {fail} xato")
raise SystemExit(1 if fail else 0)
