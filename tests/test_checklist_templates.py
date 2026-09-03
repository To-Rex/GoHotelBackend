#!/usr/bin/env python3
"""Vazifa bandlari (checklist) qoidalari.

Ishga tushirish:  python tests/test_checklist_templates.py

Nozik joylar:

1. STANDART ro'yxat faqat mehmonxona hech narsa kiritmaganda ishlatiladi.
   Administrator o'z ro'yxatini kiritsa, standart bandlar unga
   QO'SHILMAYDI — butunlay o'rnini bo'shatadi.

2. Administrator ro'yxatni BO'SHATSA, standart ro'yxat qaytmaydi. Bu ongli
   tanlov. Shu sabab bo'shatishda qatorlar o'chirilmaydi, faqat o'chirib
   qo'yiladi — aks holda "hech qachon sozlanmagan" holatidan farqi
   qolmasdi.

3. Bandlar vazifaga NUSXA bo'lib tushadi. Takroriy chaqiruv ro'yxatni
   ikkilantirmasligi kerak.

4. Ro'yxatni faqat ADMINISTRATOR o'zgartiradi; farrosh o'qiy oladi.
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.application.services.checklist_template_service import (  # noqa: E402
    DEFAULT_TEMPLATES,
    MAX_TITLE_LENGTH,
    TASK_TYPES,
    ChecklistTemplateService,
    _clean_task_type,
    _clean_title,
    _require_admin,
)
from app.core.exceptions import ForbiddenException, ValidationException  # noqa: E402

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  OK   {label:<54} {got}")
    else:
        fail += 1
        print(f"  XATO {label:<54} kutilgan {want}, chiqdi {got}")


HOTEL = uuid.uuid4()
ADMIN = {"user_type": "ADMIN", "id": uuid.uuid4()}
SUPER = {"user_type": "SUPER_ADMIN", "id": uuid.uuid4()}
STAFF = {"user_type": "EMPLOYEE", "id": uuid.uuid4()}


class FakeRow:
    def __init__(self, task_type, title, sort_order=0, is_active=True):
        self.id = uuid.uuid4()
        self.hotel_id = HOTEL
        self.task_type = task_type
        self.title = title
        self.sort_order = sort_order
        self.is_active = is_active


class FakeTask:
    def __init__(self, task_type="CLEANING"):
        self.id = uuid.uuid4()
        self.hotel_id = HOTEL
        self.task_type = task_type


class FakeResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value or []


class FakeSession:
    """`_rows` va `attach_to_task` uchun yetarli minimal sessiya."""

    def __init__(self, rows=None, has_items=False):
        self.rows = rows or []
        self.has_items = has_items
        self.added = []
        self.flushes = 0

    async def execute(self, stmt):
        text = str(stmt)
        if "checklist_items" in text:
            return FakeResult((1,) if self.has_items else None)
        return FakeResult(self.rows)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1


def svc(session):
    s = ChecklistTemplateService.__new__(ChecklistTemplateService)
    s.session = session
    return s


print("--- standart ro'yxat ---")
check("barcha vazifa turlari qamralgan", sorted(DEFAULT_TEMPLATES), sorted(TASK_TYPES))
for task_type, items in DEFAULT_TEMPLATES.items():
    check(f"{task_type}: bandlar bor", len(items) > 0, True)
    check(f"{task_type}: takror yo'q", len(set(items)), len(items))
    check(f"{task_type}: bo'sh nom yo'q", all(t.strip() for t in items), True)
# Foydalanuvchi aytgan namunalar standart ro'yxatda bo'lishi kerak
cleaning = DEFAULT_TEMPLATES["CLEANING"]
check("xonani tozalash", "Xonani tozalash" in cleaning, True)
check("shampun va sovun", any("Shampun" in t for t in cleaning), True)
check("joylarni to'g'rilash", any("to'g'rilash" in t for t in cleaning), True)


print("\n--- ruxsat ---")


def admin_ok(user):
    try:
        _require_admin(user)
        return None
    except ForbiddenException as e:
        return e.error_code


check("administrator", admin_ok(ADMIN), None)
check("super administrator", admin_ok(SUPER), None)
check("oddiy xodim", admin_ok(STAFF), "ADMIN_ONLY")


print("\n--- nom tekshiruvi ---")


def title(value):
    try:
        return _clean_title(value)
    except ValidationException as e:
        return e.error_code


check("ortiqcha bo'shliq olib tashlanadi", title("  Xonani tozalash  "), "Xonani tozalash")
check("bo'sh nom", title(""), "TITLE_REQUIRED")
check("faqat bo'shliq", title("   "), "TITLE_REQUIRED")
check("None", title(None), "TITLE_REQUIRED")
check("juda uzun nom", title("x" * (MAX_TITLE_LENGTH + 1)), "TITLE_TOO_LONG")
check("chegaradagi nom o'tadi", len(title("x" * MAX_TITLE_LENGTH)), MAX_TITLE_LENGTH)


print("\n--- vazifa turi ---")


def task_type(value):
    try:
        return _clean_task_type(value)
    except ValidationException as e:
        return e.error_code


check("kichik harf kattaga aylanadi", task_type("cleaning"), "CLEANING")
check("bo'shliq olib tashlanadi", task_type(" MAINTENANCE "), "MAINTENANCE")
check("noma'lum tur", task_type("YOQ_BUNDAY"), "INVALID_TASK_TYPE")
check("bo'sh qiymat", task_type(""), "INVALID_TASK_TYPE")


print("\n--- qaysi bandlar ishlatiladi ---")


def titles_for(rows, task_type_value="CLEANING"):
    return asyncio.run(svc(FakeSession(rows)).titles_for(HOTEL, task_type_value))


check(
    "hech narsa kiritilmagan -> standart",
    titles_for([]),
    list(DEFAULT_TEMPLATES["CLEANING"]),
)
own = [
    FakeRow("CLEANING", "Xonani tozalash", 0),
    FakeRow("CLEANING", "Sovunni almashtirish", 1),
]
check(
    "o'z ro'yxati -> faqat o'sha",
    titles_for(own),
    ["Xonani tozalash", "Sovunni almashtirish"],
)
check("standart qo'shilmaydi", len(titles_for(own)), 2)

mixed = [
    FakeRow("CLEANING", "Faol band", 0),
    FakeRow("CLEANING", "O'chirilgan band", 1, is_active=False),
]
check("o'chirilgan band tushmaydi", titles_for(mixed), ["Faol band"])

all_off = [
    FakeRow("CLEANING", "Birinchi", 0, is_active=False),
    FakeRow("CLEANING", "Ikkinchi", 1, is_active=False),
]
check(
    "hammasi o'chirilgan -> bo'sh, standart QAYTMAYDI",
    titles_for(all_off),
    [],
)

# Boshqa turdagi bandlar aralashib ketmasligi kerak
other = [FakeRow("MAINTENANCE", "Ta'mir bandi", 0)]
check(
    "boshqa turning bandlari aralashmaydi",
    titles_for(other, "CLEANING"),
    list(DEFAULT_TEMPLATES["CLEANING"]),
)
check("noma'lum tur uchun bo'sh", titles_for([], "YOQ_BUNDAY"), [])


print("\n--- vazifaga nusxalash ---")
session = FakeSession([])
count = asyncio.run(svc(session).attach_to_task(FakeTask("CLEANING")))
check("standart bandlar qo'shildi", count, len(DEFAULT_TEMPLATES["CLEANING"]))
check("qatorlar sessiyaga berildi", len(session.added), count)
check("tartib 0 dan boshlanadi", session.added[0].sort_order, 0)
check("tartib ketma-ket", [r.sort_order for r in session.added], list(range(count)))
check("hech biri belgilanmagan", all(not r.is_completed for r in session.added), True)
check("nomlar standartga mos",
      [r.title for r in session.added], list(DEFAULT_TEMPLATES["CLEANING"]))

session = FakeSession(own)
count = asyncio.run(svc(session).attach_to_task(FakeTask("CLEANING")))
check("o'z ro'yxati nusxalanadi", count, 2)

# TAKRORIY chaqiruv ro'yxatni ikkilantirmasligi kerak
session = FakeSession([], has_items=True)
count = asyncio.run(svc(session).attach_to_task(FakeTask("CLEANING")))
check("vazifada band bor bo'lsa qo'shilmaydi", count, 0)
check("hech narsa qo'shilmadi", len(session.added), 0)

session = FakeSession(all_off)
count = asyncio.run(svc(session).attach_to_task(FakeTask("CLEANING")))
check("ongli ravishda bo'sh ro'yxat", count, 0)
check("bo'sh ro'yxatda flush chaqirilmaydi", session.flushes, 0)

print(f"\nJami: {ok} ok, {fail} xato")
raise SystemExit(1 if fail else 0)
