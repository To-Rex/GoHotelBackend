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
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, other_rows=None):
        #: Xonadagi BOSHQA faol vazifalar — (turi, rejalashtirilgan sana)
        self.other_rows = list(other_rows or [])
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def execute(self, _stmt):
        return FakeResult(list(self.other_rows))


class FakeRoomRepo:
    def __init__(self, room):
        self.room = room

    async def get_by_id(self, room_id, hotel_id):
        return self.room if self.room and self.room.id == room_id else None

    async def update(self, room, **values):
        for k, v in values.items():
            setattr(room, k, v)
        return room


def service(room, other_rows=None):
    svc = HousekeepingService.__new__(HousekeepingService)
    svc.session = FakeSession(other_rows)
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
svc = service(room)
asyncio.run(
    svc._release_task_room_status(
        FakeTask("MAINTENANCE", room.id, status="COMPLETED"), HOTEL, USER
    )
)
check("oxirgi vazifa yopildi", room.current_status, "AVAILABLE")

room = FakeRoom("MAINTENANCE")
svc = service(room, [("MAINTENANCE", None)])
asyncio.run(
    svc._release_task_room_status(
        FakeTask("MAINTENANCE", room.id, status="COMPLETED"), HOTEL, USER
    )
)
check("boshqa ochiq vazifa bor", room.current_status, "MAINTENANCE")

room = FakeRoom("MAINTENANCE")
svc = service(room)
asyncio.run(
    svc._release_task_room_status(
        FakeTask("MAINTENANCE", room.id, status="CANCELLED"), HOTEL, USER
    )
)
check("bekor qilindi", room.current_status, "AVAILABLE")

# Xona boshqa holatga o'tib ketgan bo'lsa tegilmaydi
room = FakeRoom("OCCUPIED")
svc = service(room)
asyncio.run(
    svc._release_task_room_status(
        FakeTask("MAINTENANCE", room.id, status="COMPLETED"), HOTEL, USER
    )
)
check("xona oralig'ida band bo'lib qolgan", room.current_status, "OCCUPIED")

print("--- tozalash tugadi, boshqa ish davom etmoqda ---")
# Ta'mir hali ochiq — xona "Bo'sh" emas, "Ta'mirda" bo'ladi: aks holda
# tugallanmagan ta'mirdagi xonaga bron qilish mumkin bo'lib qolardi
room = FakeRoom("CLEANING")
svc = service(room, [("MAINTENANCE", None)])
asyncio.run(
    svc._release_task_room_status(
        FakeTask("CLEANING", room.id, status="COMPLETED"), HOTEL, USER
    )
)
check("tozalash tugadi, ta'mir ochiq", room.current_status, "MAINTENANCE")

room = FakeRoom("CLEANING")
svc = service(room, [("INSPECTION", None)])
asyncio.run(
    svc._release_task_room_status(
        FakeTask("CLEANING", room.id, status="COMPLETED"), HOTEL, USER
    )
)
check("tozalash tugadi, tekshiruv ochiq", room.current_status, "INSPECTION")

# Kelgusi haftaga rejalashtirilgan ta'mir xonani band qilmaydi
room = FakeRoom("CLEANING")
svc = service(room, [("MAINTENANCE", today + timedelta(days=7))])
asyncio.run(
    svc._release_task_room_status(
        FakeTask("CLEANING", room.id, status="COMPLETED"), HOTEL, USER
    )
)
check("kelgusi ta'mir to'sqinlik qilmaydi", room.current_status, "AVAILABLE")

# Ta'mir tugadi, tozalash hali ochiq — xona tozalashga o'tadi
room = FakeRoom("MAINTENANCE")
svc = service(room, [("CLEANING", None)])
asyncio.run(
    svc._release_task_room_status(
        FakeTask("MAINTENANCE", room.id, status="COMPLETED"), HOTEL, USER
    )
)
check("ta'mir tugadi, tozalash ochiq", room.current_status, "CLEANING")

# Chuqur tozalash ham CLEANING holatini talab qiladi — xona o'zgarmaydi
room = FakeRoom("CLEANING")
svc = service(room, [("DEEP_CLEANING", None)])
asyncio.run(
    svc._release_task_room_status(
        FakeTask("CLEANING", room.id, status="COMPLETED"), HOTEL, USER
    )
)
check("chuqur tozalash davom etmoqda", room.current_status, "CLEANING")

print("--- bron: faol ta'mir vazifasi to'sadi ---")
from app.application.services.reservation_service import ReservationService
from app.core.exceptions import ConflictException


class FakeBookRoom:
    def __init__(self, status):
        self.id = uuid.uuid4()
        self.room_number = "101"
        self.current_status = status


def res_service(task_rows):
    svc = ReservationService.__new__(ReservationService)
    svc.session = FakeSession(task_rows)
    return svc


def try_book(status, task_rows, check_in_delta=3):
    room = FakeBookRoom(status)
    svc = res_service(task_rows)
    try:
        asyncio.run(
            svc._assert_room_bookable(
                room,
                "DAILY",
                today + timedelta(days=check_in_delta),
                today + timedelta(days=check_in_delta + 1),
            )
        )
        return "OK"
    except ConflictException:
        return "BLOKLANDI"


check("tozalashda + ochiq ta'mir", try_book("CLEANING", [("MAINTENANCE", None)]), "BLOKLANDI")
check("bo'sh + ochiq tekshiruv", try_book("AVAILABLE", [("INSPECTION", None)]), "BLOKLANDI")
check("tozalashda, vazifasiz — kelgusiga ochiq", try_book("CLEANING", []), "OK")
check("bo'sh, vazifasiz", try_book("AVAILABLE", []), "OK")
check(
    "kelgusi haftadagi ta'mir to'smaydi",
    try_book("AVAILABLE", [("MAINTENANCE", today + timedelta(days=7))]),
    "OK",
)

print(f"\nJami: {ok} ok, {fail} xato")
raise SystemExit(1 if fail else 0)
