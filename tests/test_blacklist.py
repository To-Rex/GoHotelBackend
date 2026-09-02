#!/usr/bin/env python3
"""Qora ro'yxat qoidalari.

Ishga tushirish:  python tests/test_blacklist.py

Nozik joylar: taqiq STANDART holda yoqiq bo'lishi (administrator kimnidir
ro'yxatga qo'shganda xizmat ko'rsatilmasligini kutadi), sabab majburiyligi
va hamrohlarning ham tekshirilishi — aks holda ro'yxatdagi odam boshqa
birovning nomiga hamroh bo'lib kirib ketardi.
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.application.services.blacklist_service import (  # noqa: E402
    DEFAULT_BLOCK_BOOKING,
    BlacklistService,
    resolve_block_booking,
)
from app.core.exceptions import (  # noqa: E402
    ConflictException,
    ForbiddenException,
    ValidationException,
)

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label:<54} {got}")
    else:
        fail += 1
        print(f"  XATO {label:<54} kutilgan {want}, chiqdi {got}")


print("--- sozlama ---")
check("standart holda TAQIQ yoqiq", DEFAULT_BLOCK_BOOKING, True)
check("sozlanmagan mehmonxona", resolve_block_booking(None), True)
check("bo'sh sozlama", resolve_block_booking({}), True)
check(
    "ochiq qilingan",
    resolve_block_booking({"blacklist_policy": {"block_booking": False}}),
    False,
)
check(
    "yoqilgan",
    resolve_block_booking({"blacklist_policy": {"block_booking": True}}),
    True,
)
check(
    "buzuq qiymat standartga qaytadi",
    resolve_block_booking({"blacklist_policy": {"block_booking": "ha"}}),
    True,
)


class FakeGuest:
    def __init__(self, blacklisted=False):
        self.id = uuid.uuid4()
        self.first_name = "Aziz"
        self.last_name = "Karimov"
        self.blacklisted_at = "2026-09-01" if blacklisted else None
        self.blacklist_reason = "Janjal ko'targan" if blacklisted else None
        self.blacklisted_by = None


class FakeHotel:
    def __init__(self, settings=None):
        self.settings = settings


class FakeResult:
    def __init__(self, value, mode="scalar"):
        self.value = value
        self.mode = mode

    def scalar_one_or_none(self):
        return self.value

    def first(self):
        return self.value


class FakeSession:
    def __init__(self, guest=None, hotel=None, blacklisted_row=None):
        self.guest = guest
        self.hotel = hotel
        self.blacklisted_row = blacklisted_row
        self.flushed = False

    async def flush(self):
        self.flushed = True

    async def get(self, _model, _pk):
        return self.hotel

    async def execute(self, _stmt):
        # `assert_bookable` `first()` bilan o'qiydi, qolganlari
        # `scalar_one_or_none()` bilan
        if self.blacklisted_row is not None or self.guest is None:
            return FakeResult(self.blacklisted_row)
        return FakeResult(self.guest)


def svc(session):
    s = BlacklistService.__new__(BlacklistService)
    s.session = session
    return s


ADMIN = {"user_type": "ADMIN", "id": uuid.uuid4()}
STAFF = {"user_type": "EMPLOYEE", "id": uuid.uuid4()}


def add(session, user, reason):
    try:
        asyncio.run(svc(session).add(uuid.uuid4(), uuid.uuid4(), reason, user))
        return None
    except (ForbiddenException, ValidationException, ConflictException) as e:
        return e.error_code


print("--- ro'yxatga qo'shish ---")
check(
    "administrator qo'sha oladi",
    add(FakeSession(guest=FakeGuest()), ADMIN, "Janjal ko'targan"),
    None,
)
check(
    "oddiy xodim qo'sha olmaydi",
    add(FakeSession(guest=FakeGuest()), STAFF, "Janjal"),
    "ADMIN_ONLY",
)
check(
    "sabab majburiy",
    add(FakeSession(guest=FakeGuest()), ADMIN, ""),
    "REASON_REQUIRED",
)
check(
    "faqat bo'shliqdan iborat sabab ham qabul qilinmaydi",
    add(FakeSession(guest=FakeGuest()), ADMIN, "   "),
    "REASON_REQUIRED",
)
check(
    "allaqachon ro'yxatdagi mehmon",
    add(FakeSession(guest=FakeGuest(blacklisted=True)), ADMIN, "Yana"),
    "ALREADY_BLACKLISTED",
)

print("--- ro'yxatdan chiqarish ---")
guest = FakeGuest(blacklisted=True)
session = FakeSession(guest=guest)
asyncio.run(svc(session).remove(guest.id, uuid.uuid4(), ADMIN))
check("uchala maydon ham tozalanadi",
      (guest.blacklisted_at, guest.blacklist_reason, guest.blacklisted_by),
      (None, None, None))

try:
    asyncio.run(svc(FakeSession(guest=FakeGuest(True))).remove(uuid.uuid4(), uuid.uuid4(), STAFF))
    check("oddiy xodim chiqara olmaydi", "ruxsat berildi", "ADMIN_ONLY")
except ForbiddenException as e:
    check("oddiy xodim chiqara olmaydi", e.error_code, "ADMIN_ONLY")


print("--- bron ochishda tekshiruv ---")


def bookable(blacklisted_row, settings):
    session = FakeSession(hotel=FakeHotel(settings), blacklisted_row=blacklisted_row)
    try:
        asyncio.run(svc(session).assert_bookable([uuid.uuid4()], uuid.uuid4()))
        return None
    except ConflictException as e:
        return e.error_code


check("toza mehmon o'tadi", bookable(None, None), None)
check(
    "ro'yxatdagi mehmon to'siladi",
    bookable(("Aziz", "Karimov", "Janjal ko'targan"), None),
    "GUEST_BLACKLISTED",
)
check(
    "sozlama o'chirilgan bo'lsa o'tadi",
    bookable(
        ("Aziz", "Karimov", "Janjal"),
        {"blacklist_policy": {"block_booking": False}},
    ),
    None,
)
check("bo'sh ro'yxat tekshirilmaydi",
      (lambda: asyncio.run(svc(FakeSession()).assert_bookable([], uuid.uuid4())))(),
      None)

print(f"\nJami: {ok} ok, {fail} xato")
raise SystemExit(1 if fail else 0)
