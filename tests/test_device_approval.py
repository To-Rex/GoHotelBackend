#!/usr/bin/env python3
"""Qurilma tasdiqlash qoidalari.

Ishga tushirish:  python tests/test_device_approval.py

Asosiy tekshiruv — rad etishdan OLDIN commit. `get_db` har xatoda sessiyani
rollback qiladi, ya'ni commitsiz qurilma yozuvi bekor bo'lardi: ro'yxat
bo'sh qolib, administrator nimani tasdiqlashini bilmasdi. Aynan shu xato
ishlab chiqarishda chiqdi.
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.application.services.device_service import (  # noqa: E402
    DEVICE_CHECK_EXEMPT_TYPES,
    DeviceService,
)
from app.core.exceptions import ForbiddenException  # noqa: E402

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label:<54} {got}")
    else:
        fail += 1
        print(f"  XATO {label:<54} kutilgan {want}, chiqdi {got}")


class FakeUser:
    def __init__(self, user_type="EMPLOYEE", hotel_id=None):
        self.id = uuid.uuid4()
        self.username = "xodim"
        self.user_type = user_type
        self.hotel_id = hotel_id if hotel_id is not None else uuid.uuid4()


class FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class FakeSession:
    """Sessiya o'rnini bosuvchi: qaysi amal qaysi tartibda bo'lganini yozadi."""

    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.events = []

    def add(self, obj):
        self.added.append(obj)
        self.events.append("add")

    async def flush(self):
        self.events.append("flush")

    async def commit(self):
        self.events.append("commit")

    async def execute(self, _stmt):
        return FakeResult(self.existing)


class FakeDevice:
    def __init__(self, status):
        self.status = status
        self.last_seen_at = None
        self.last_user_id = None
        self.user_agent = None
        self.ip_address = None


def run(user, session, device_id="dev-1"):
    svc = DeviceService.__new__(DeviceService)
    svc.session = session
    return asyncio.run(
        svc.ensure_allowed(user, device_id, user_agent="UA", ip_address="1.2.3.4")
    )


def rejects(user, session, device_id="dev-1"):
    try:
        run(user, session, device_id)
        return None
    except ForbiddenException as e:
        return e.error_code


print("--- administrator tekshiruvdan ozod ---")
for t in DEVICE_CHECK_EXEMPT_TYPES:
    ses = FakeSession()
    check(f"{t} o'tadi", rejects(FakeUser(t), ses), None)
    check(f"{t} uchun yozuv yaratilmaydi", ses.added, [])

print("--- tasdiqlangan qurilma ---")
ses = FakeSession(FakeDevice("APPROVED"))
check("xodim o'tadi", rejects(FakeUser(), ses), None)
check("commit qilinmaydi — so'rov davom etadi", "commit" in ses.events, False)

print("--- yangi qurilma ---")
ses = FakeSession(None)
check("rad etiladi", rejects(FakeUser(), ses), "DEVICE_PENDING")
check("yozuv yaratildi", len(ses.added), 1)
check("yaratilgan yozuv PENDING", ses.added[0].status, "PENDING")
# ENG MUHIMI: commit xatodan oldin bo'lishi kerak, aks holda rollback
# yozuvni yo'q qiladi
check("commit qilindi", "commit" in ses.events, True)
check("commit add'dan keyin", ses.events.index("commit") > ses.events.index("add"), True)

print("--- kutayotgan qurilma qayta urinsa ---")
pending = FakeDevice("PENDING")
ses = FakeSession(pending)
check("yana rad etiladi", rejects(FakeUser(), ses), "DEVICE_PENDING")
check("yangi yozuv yaratilmaydi", ses.added, [])
check("oxirgi urinish yangilandi", pending.ip_address, "1.2.3.4")
check("commit qilindi", "commit" in ses.events, True)

print("--- taqiqlangan qurilma ---")
ses = FakeSession(FakeDevice("BLOCKED"))
check("rad etiladi", rejects(FakeUser(), ses), "DEVICE_BLOCKED")

print("--- qurilma ID'si yo'q ---")
ses = FakeSession(None)
check("rad etiladi", rejects(FakeUser(), ses, device_id=""), "DEVICE_UNKNOWN")
check("yozuv yaratilmaydi", ses.added, [])

print("--- mehmonxonasiz foydalanuvchi ---")
ses = FakeSession(None)
user = FakeUser()
user.hotel_id = None
check("tekshirilmaydi", rejects(user, ses), None)

print("--- qurilma tasdiqlanadi, XODIM emas ---")
# Bir marta tasdiqlangan qurilmada mehmonxonaning istalgan xodimi ishlashi
# kerak: resepsiya smenasi almashganda qayta tasdiq so'ralmasin
hotel = uuid.uuid4()
approved = FakeDevice("APPROVED")
for who in ("resepsiya-1", "resepsiya-2", "menejer"):
    ses = FakeSession(approved)
    u = FakeUser(hotel_id=hotel)
    u.username = who
    check(f"{who} o'tadi", rejects(u, ses), None)


print("--- ochiq sessiya: qurilma huquqi bekor qilinsa ---")


def session_check(status_or_missing, user_type="EMPLOYEE"):
    """assert_session_allowed — har so'rovda ishlaydigan tekshiruv."""

    class Res:
        def __init__(self, v):
            self.v = v

        def scalar_one_or_none(self):
            return self.v

    class Ses:
        async def execute(self, _s):
            return Res(status_or_missing)

    svc = DeviceService.__new__(DeviceService)
    svc.session = Ses()
    try:
        asyncio.run(svc.assert_session_allowed(uuid.uuid4(), "dev-1", user_type))
        return None
    except ForbiddenException as e:
        return e.error_code


check("tasdiqlangan — ishlayveradi", session_check("APPROVED"), None)
check("taqiqlangan — to'xtatiladi", session_check("BLOCKED"), "DEVICE_BLOCKED")
check("tasdiq bekor qilingan — to'xtatiladi", session_check("PENDING"), "DEVICE_PENDING")
check("o'chirilgan — to'xtatiladi", session_check(None), "DEVICE_REVOKED")
check("administrator sessiyasiga tegilmaydi", session_check(None, "ADMIN"), None)

print(f"\nJami: {ok} ok, {fail} xato")
raise SystemExit(1 if fail else 0)
